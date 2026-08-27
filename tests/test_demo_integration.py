from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Sequence

from coding_agent.agent import AgentRunner
from coding_agent.context import ContextManager
from coding_agent.llm import AssistantResponse, ToolCall
from coding_agent.tools import ToolRegistry, create_command_tool, create_file_tools
from coding_agent.workspace import Workspace


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEMO_TEMPLATE = PROJECT_ROOT / "examples" / "buggy_project"


def _response(content: str = "", *calls: tuple[str, str, dict]) -> AssistantResponse:
    tool_calls = tuple(
        ToolCall(id=call_id, name=name, arguments=json.dumps(arguments))
        for call_id, name, arguments in calls
    )
    provider = {"role": "assistant", "content": content, "reasoning_content": "private"}
    if tool_calls:
        provider["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in tool_calls
        ]
    return AssistantResponse(content, tool_calls, provider)


class FakeModel:
    def __init__(self, responses: list[AssistantResponse]) -> None:
        self.responses = responses
        self.requests: list[list[dict]] = []

    def complete(self, *, messages: Sequence[dict], tools: Sequence[dict]) -> AssistantResponse:
        self.requests.append(list(messages))
        return self.responses.pop(0)


def test_demo_starts_failing_then_real_harness_repairs_and_verifies(tmp_path: Path) -> None:
    demo = tmp_path / "demo"
    shutil.copytree(DEMO_TEMPLATE, demo)
    initial = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=demo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert initial.returncode != 0
    assert "failed" in initial.stdout

    model = FakeModel(
        [
            _response("", ("l", "list_files", {})),
            _response(
                "",
                (
                    "r",
                    "run_command",
                    {"argv": [sys.executable, "-m", "pytest", "-q"]},
                ),
            ),
            _response(
                "",
                ("s", "search_text", {"query": "words[:-1]", "path": "."}),
            ),
            _response(
                "",
                ("f", "read_file", {"path": "text_metrics/stats.py"}),
            ),
            _response(
                "",
                (
                    "e",
                    "edit_file",
                    {
                        "path": "text_metrics/stats.py",
                        "old_text": "return dict(Counter(words[:-1]))",
                        "new_text": "return dict(Counter(words))",
                    },
                ),
            ),
            _response(
                "",
                (
                    "v",
                    "run_command",
                    {"argv": [sys.executable, "-m", "pytest", "-q"]},
                ),
            ),
            _response("Fixed the dropped-final-word bug; all tests pass."),
        ]
    )
    workspace = Workspace(demo)
    registry = ToolRegistry(
        [*create_file_tools(workspace), create_command_tool(workspace, default_timeout=30)]
    )
    result = AgentRunner(
        model=model,
        tools=registry,
        context=ContextManager(system_prompt="system", original_task="fix failing tests"),
        max_steps=10,
    ).run()

    assert result.status == "completed"
    assert result.verification_observed
    final = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=demo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert final.returncode == 0, final.stdout + final.stderr

