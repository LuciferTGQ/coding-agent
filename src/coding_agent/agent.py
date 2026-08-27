"""Single-agent model/tool/environment feedback loop."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable

from coding_agent.context import ContextManager
from coding_agent.llm import ModelClient, ModelError
from coding_agent.tools.registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class AgentEvent:
    kind: str
    step: int
    message: str
    tool_name: str | None = None
    ok: bool | None = None


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
    ) -> None:
        self.model = model
        self.tools = tools
        self.context = context
        self.max_steps = max_steps
        self.max_consecutive_tool_errors = max_consecutive_tool_errors
        self.on_event = on_event or (lambda _: None)

    def run(self) -> AgentResult:
        workspace_changed = False
        verification_observed = False
        consecutive_tool_errors = 0

        for step in range(1, self.max_steps + 1):
            self._emit("step", step, f"Agent step {step}/{self.max_steps}")
            try:
                response = self.model.complete(
                    messages=self.context.messages(),
                    tools=self.tools.get_definitions(),
                )
            except ModelError as exc:
                raise AgentRunError(str(exc)) from exc

            if response.content:
                self._emit("model", step, response.content)

            if not response.tool_calls:
                self._emit("final", step, response.content)
                return AgentResult(
                    final_answer=response.content,
                    steps=step,
                    status="completed",
                    workspace_changed=workspace_changed,
                    verification_observed=verification_observed,
                )

            tool_messages: list[dict[str, Any]] = []
            for call in response.tool_calls:
                self._emit(
                    "tool_call",
                    step,
                    _summarize_arguments(call.arguments),
                    tool_name=call.name,
                )
                result = self.tools.execute(call.name, call.arguments)
                if result.ok:
                    consecutive_tool_errors = 0
                else:
                    consecutive_tool_errors += 1
                if result.ok and result.changed:
                    workspace_changed = True
                    verification_observed = False
                if result.ok and result.verification:
                    verification_observed = True
                self._emit(
                    "tool_result",
                    step,
                    result.message,
                    tool_name=call.name,
                    ok=result.ok,
                )
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
                return AgentResult(
                    final_answer=message,
                    steps=step,
                    status="tool_error_limit",
                    workspace_changed=workspace_changed,
                    verification_observed=verification_observed,
                )

        message = f"Stopped after reaching the maximum of {self.max_steps} agent steps."
        self._emit("stopped", self.max_steps, message, ok=False)
        return AgentResult(
            final_answer=message,
            steps=self.max_steps,
            status="max_steps",
            workspace_changed=workspace_changed,
            verification_observed=verification_observed,
        )

    def _emit(
        self,
        kind: str,
        step: int,
        message: str,
        *,
        tool_name: str | None = None,
        ok: bool | None = None,
    ) -> None:
        self.on_event(
            AgentEvent(kind=kind, step=step, message=message, tool_name=tool_name, ok=ok)
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

