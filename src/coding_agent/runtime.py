"""Composition root that wires the independently testable harness layers."""

from __future__ import annotations

from coding_agent.agent import AgentEvent, AgentRunError, AgentRunner
from coding_agent.config import Config
from coding_agent.context import ContextManager
from coding_agent.llm import DeepSeekChatClient
from coding_agent.prompts import build_system_prompt
from coding_agent.tools import ToolRegistry, create_command_tool, create_file_tools
from coding_agent.workspace import Workspace


def run_task(*, config: Config, task: str, verbose: bool = False) -> int:
    workspace = Workspace(config.workspace)
    registry = ToolRegistry(
        [
            *create_file_tools(
                workspace,
                max_read_lines=config.max_read_lines,
                max_write_chars=config.max_write_chars,
                max_search_results=config.max_search_results,
            ),
            create_command_tool(
                workspace,
                default_timeout=config.command_timeout,
                output_limit=config.tool_output_limit,
            ),
        ],
        output_limit=config.tool_output_limit,
    )
    # The credential crosses exactly one boundary: Config -> DeepSeek client.
    model = DeepSeekChatClient(
        api_key=config.api_key,
        base_url=config.base_url,
        model=config.model,
        reasoning_effort=config.reasoning_effort,
        thinking_enabled=config.thinking_enabled,
    )
    context = ContextManager(
        system_prompt=build_system_prompt(workspace.root),
        original_task=task,
        soft_budget=config.context_soft_budget,
    )
    reporter = ConsoleReporter(verbose=verbose)
    runner = AgentRunner(
        model=model,
        tools=registry,
        context=context,
        max_steps=config.max_steps,
        on_event=reporter,
    )
    try:
        result = runner.run()
    except AgentRunError as exc:
        print(f"\nAgent failed: {exc}")
        return 1
    return 0 if result.status == "completed" else 2


class ConsoleReporter:
    """Readable CLI events that deliberately exclude private reasoning."""

    def __init__(self, *, verbose: bool = False) -> None:
        self.verbose = verbose

    def __call__(self, event: AgentEvent) -> None:
        if event.kind == "step":
            print(f"\n[{event.message}]")
        elif event.kind == "model":
            print(f"Model: {event.message}")
        elif event.kind == "tool_call":
            print(f"Tool: {event.tool_name} {event.message}")
        elif event.kind == "tool_result":
            status = "ok" if event.ok else "error"
            limit = 4_000 if self.verbose else 1_200
            message = event.message
            if len(message) > limit:
                message = message[:limit] + "\n... [CLI display truncated]"
            print(f"Result ({status}): {message}")
        elif event.kind == "final":
            print(f"\nFinal Answer:\n{event.message}")
        elif event.kind == "verification":
            print(f"Verification Guard: {event.message}")
        elif event.kind == "stopped":
            print(f"\nStopped: {event.message}")
