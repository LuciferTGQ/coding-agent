"""Single-agent model/tool/environment feedback loop."""

from __future__ import annotations

from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
import json
from typing import Any, Callable

from coding_agent.context import ContextManager, SummaryCallback
from coding_agent.llm import ModelClient, ModelError, ModelStreamEvent, ToolCall
from coding_agent.tools.base import ToolResult
from coding_agent.tools.registry import ToolRegistry

MAX_PARALLEL_TOOL_WORKERS = 4


@dataclass(frozen=True, slots=True)
class AgentEvent:
    kind: str
    step: int
    message: str
    tool_name: str | None = None
    ok: bool | None = None
    call_id: str | None = None


@dataclass(frozen=True, slots=True)
class AgentResult:
    final_answer: str
    steps: int
    status: str
    workspace_changed: bool
    verification_observed: bool


class AgentRunError(RuntimeError):
    """Raised when the model provider prevents the loop from continuing."""


class AgentRunner:
    def __init__(
        self,
        *,
        model: ModelClient,
        tools: ToolRegistry,
        context: ContextManager,
        max_steps: int = 24,
        max_consecutive_tool_errors: int = 6,
        on_event: Callable[[AgentEvent], None] | None = None,
        stream: bool = False,
        should_cancel: Callable[[], bool] | None = None,
        summarizer: SummaryCallback | None = None,
        parallel_tool_names: frozenset[str] = frozenset(),
        max_parallel_tools: int = 4,
    ) -> None:
        self.model = model
        self.tools = tools
        self.context = context
        self.max_steps = max_steps
        self.max_consecutive_tool_errors = max_consecutive_tool_errors
        self.on_event = on_event or (lambda _: None)
        self.stream = stream
        self.should_cancel = should_cancel or (lambda: False)
        self.summarizer = summarizer
        self.parallel_tool_names = parallel_tool_names
        self.max_parallel_tools = max(
            1, min(MAX_PARALLEL_TOOL_WORKERS, max_parallel_tools)
        )
        self._current_step = 0

    def run(self, task: str | None = None) -> AgentResult:
        if task is not None:
            self.context.start_turn(task)
        elif not self.context.has_active_turn:
            raise RuntimeError("AgentRunner requires an active context turn or a task")
        workspace_changed = False
        verification_observed = False
        consecutive_tool_errors = 0
        workspace_revision = 0
        verified_revision = 0
        reminded_revision = 0

        for step in range(1, self.max_steps + 1):
            self._current_step = step
            if self.should_cancel():
                return self._cancelled(step - 1, workspace_changed, verification_observed)
            self.context.compact(self.summarizer)
            self._emit("step", step, f"Agent step {step}/{self.max_steps}")
            try:
                if self.stream and hasattr(self.model, "complete_stream"):
                    response = self.model.complete_stream(
                        messages=self.context.messages(),
                        tools=self.tools.get_definitions(),
                        on_event=self._emit_stream,
                    )
                else:
                    response = self.model.complete(
                        messages=self.context.messages(),
                        tools=self.tools.get_definitions(),
                    )
            except ModelError as exc:
                self.context.abandon_turn()
                raise AgentRunError(str(exc)) from exc

            if response.content and not self.stream:
                self._emit("model", step, response.content)

            if not response.tool_calls:
                if (
                    workspace_revision > verified_revision
                    and reminded_revision < workspace_revision
                ):
                    feedback = (
                        "You modified the workspace but have not verified the latest changes. "
                        "Run an appropriate test, build, lint, or program execution if one is "
                        "available. If no meaningful automated verification exists, explain "
                        "that explicitly in your next final answer."
                    )
                    reminded_revision = workspace_revision
                    self.context.add_feedback(response.provider_message, feedback)
                    self._emit("verification", step, feedback, ok=False)
                    continue
                self._emit("final", step, response.content)
                self.context.finish_turn(response.provider_message)
                return AgentResult(
                    final_answer=response.content,
                    steps=step,
                    status="completed",
                    workspace_changed=workspace_changed,
                    verification_observed=verification_observed,
                )

            if self.should_cancel():
                return self._cancelled(step, workspace_changed, verification_observed)
            results = self._execute_tool_calls(response.tool_calls, step)
            tool_messages: list[dict[str, Any]] = []
            for call, result in zip(response.tool_calls, results):
                if result.ok:
                    consecutive_tool_errors = 0
                else:
                    consecutive_tool_errors += 1
                if result.ok and result.changed:
                    workspace_changed = True
                    verification_observed = False
                    workspace_revision += 1
                if result.ok and result.verification:
                    verification_observed = True
                    verified_revision = workspace_revision
                tool_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": result.to_model_text(),
                    }
                )

            self.context.add_interaction(response.provider_message, tool_messages)
            if consecutive_tool_errors >= self.max_consecutive_tool_errors:
                message = (
                    "Stopped after repeated tool errors. Review the latest tool feedback "
                    "and retry with a more specific task."
                )
                self._emit("stopped", step, message, ok=False)
                self.context.abandon_turn({"role": "assistant", "content": message})
                return AgentResult(
                    final_answer=message,
                    steps=step,
                    status="tool_error_limit",
                    workspace_changed=workspace_changed,
                    verification_observed=verification_observed,
                )

        message = f"Stopped after reaching the maximum of {self.max_steps} agent steps."
        self._emit("stopped", self.max_steps, message, ok=False)
        self.context.abandon_turn({"role": "assistant", "content": message})
        return AgentResult(
            final_answer=message,
            steps=self.max_steps,
            status="max_steps",
            workspace_changed=workspace_changed,
            verification_observed=verification_observed,
        )

    def _execute_tool_calls(
        self, calls: tuple[ToolCall, ...], step: int
    ) -> list[ToolResult]:
        parallel = (
            len(calls) > 1
            and bool(self.parallel_tool_names)
            and all(call.name in self.parallel_tool_names for call in calls)
        )
        if parallel:
            for call in calls:
                self._emit_tool_call(step, call)
            with ThreadPoolExecutor(
                max_workers=min(self.max_parallel_tools, len(calls)),
                thread_name_prefix="coding-agent-child",
            ) as pool:
                futures = [
                    pool.submit(self.tools.execute, call.name, call.arguments)
                    for call in calls
                ]
                results = [future.result() for future in futures]
            for call, result in zip(calls, results):
                self._emit_tool_result(step, call, result)
            return results

        results: list[ToolResult] = []
        for call in calls:
            self._emit_tool_call(step, call)
            result = self.tools.execute(call.name, call.arguments)
            self._emit_tool_result(step, call, result)
            results.append(result)
        return results

    def _emit_tool_call(self, step: int, call: ToolCall) -> None:
        self._emit(
            "tool_call",
            step,
            _summarize_arguments(call.arguments),
            tool_name=call.name,
            call_id=call.id,
        )

    def _emit_tool_result(self, step: int, call: ToolCall, result: ToolResult) -> None:
        self._emit(
            "tool_result",
            step,
            result.message,
            tool_name=call.name,
            ok=result.ok,
            call_id=call.id,
        )

    def _emit(
        self,
        kind: str,
        step: int,
        message: str,
        *,
        tool_name: str | None = None,
        ok: bool | None = None,
        call_id: str | None = None,
    ) -> None:
        self.on_event(
            AgentEvent(
                kind=kind,
                step=step,
                message=message,
                tool_name=tool_name,
                ok=ok,
                call_id=call_id,
            )
        )

    def _emit_stream(self, event: ModelStreamEvent) -> None:
        self._emit(event.kind, self._current_step, event.delta)

    def _cancelled(
        self, steps: int, workspace_changed: bool, verification_observed: bool
    ) -> AgentResult:
        message = "Stopped at a safe agent boundary by user request."
        if self.context.has_active_turn:
            self.context.abandon_turn({"role": "assistant", "content": message})
        self._emit("stopped", steps, message, ok=False)
        return AgentResult(
            final_answer=message,
            steps=steps,
            status="cancelled",
            workspace_changed=workspace_changed,
            verification_observed=verification_observed,
        )


def _summarize_arguments(raw: str, limit: int = 400) -> str:
    try:
        arguments = json.loads(raw)
    except json.JSONDecodeError:
        return f"invalid JSON ({len(raw)} characters)"
    if not isinstance(arguments, dict):
        return f"non-object JSON ({type(arguments).__name__})"
    summarized: dict[str, Any] = {}
    for key, value in arguments.items():
        if key in {"content", "old_text", "new_text"} and isinstance(value, str):
            summarized[key] = f"<{len(value)} characters>"
        else:
            summarized[key] = value
    rendered = json.dumps(summarized, ensure_ascii=False)
    return rendered if len(rendered) <= limit else rendered[:limit] + "..."
