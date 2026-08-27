from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Sequence

from coding_agent.agent import AgentRunner
from coding_agent.context import ContextManager
from coding_agent.llm import AssistantResponse, ToolCall
from coding_agent.tools import ToolRegistry, create_command_tool, create_file_tools
from coding_agent.workspace import Workspace


def _response(content: str = "", *calls: tuple[str, str, dict]) -> AssistantResponse:
    tool_calls = tuple(
        ToolCall(id=call_id, name=name, arguments=json.dumps(arguments))
        for call_id, name, arguments in calls
    )
    message = {"role": "assistant", "content": content, "reasoning_content": "private"}
    if tool_calls:
        message["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in tool_calls
        ]
    return AssistantResponse(content=content, tool_calls=tool_calls, provider_message=message)


class FakeModel:
    def __init__(self, responses: list[AssistantResponse]) -> None:
        self.responses = responses
        self.requests: list[list[dict]] = []

    def complete(self, *, messages: Sequence[dict], tools: Sequence[dict]) -> AssistantResponse:
        assert tools
        self.requests.append(list(messages))
        return self.responses.pop(0)


def _runner(tmp_path: Path, model: FakeModel, *, max_steps: int = 10) -> AgentRunner:
    workspace = Workspace(tmp_path)
    registry = ToolRegistry(
        [*create_file_tools(workspace), create_command_tool(workspace, default_timeout=5)]
    )
    return AgentRunner(
        model=model,
        tools=registry,
        context=ContextManager(system_prompt="system", original_task="fix it"),
        max_steps=max_steps,
    )


def test_real_tools_complete_read_edit_execute_final_flow(tmp_path: Path) -> None:
    target = tmp_path / "value.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    model = FakeModel(
        [
            _response("", ("r", "read_file", {"path": "value.py"})),
            _response(
                "",
                (
                    "e",
                    "edit_file",
                    {"path": "value.py", "old_text": "VALUE = 1", "new_text": "VALUE = 2"},
                ),
            ),
            _response(
                "",
                (
                    "x",
                    "run_command",
                    {
                        "argv": [sys.executable, "value.py"]
                    },
                ),
            ),
            _response("Fixed VALUE and verified it."),
        ]
    )

    result = _runner(tmp_path, model).run()

    assert result.status == "completed"
    assert result.workspace_changed
    assert result.verification_observed
    assert target.read_text(encoding="utf-8") == "VALUE = 2\n"
    assert any(message["role"] == "tool" for message in model.requests[-1])


def test_multiple_calls_are_executed_in_order(tmp_path: Path) -> None:
    model = FakeModel(
        [
            _response(
                "",
                ("a", "write_file", {"path": "a.txt", "content": "a"}),
                ("b", "write_file", {"path": "b.txt", "content": "b"}),
            ),
            _response("done"),
            _response("Created both text files; no meaningful automated verification exists."),
        ]
    )
    result = _runner(tmp_path, model).run()
    assert result.status == "completed"
    assert (tmp_path / "a.txt").exists() and (tmp_path / "b.txt").exists()
    tool_messages = [message for message in model.requests[1] if message["role"] == "tool"]
    assert [message["tool_call_id"] for message in tool_messages] == ["a", "b"]


def test_malformed_call_becomes_feedback_and_loop_recovers(tmp_path: Path) -> None:
    malformed = ToolCall(id="bad", name="read_file", arguments="{")
    model = FakeModel(
        [
            AssistantResponse(
                content="",
                tool_calls=(malformed,),
                provider_message={
                    "role": "assistant",
                    "content": None,
                    "reasoning_content": "private",
                    "tool_calls": [
                        {
                            "id": "bad",
                            "type": "function",
                            "function": {"name": "read_file", "arguments": "{"},
                        }
                    ],
                },
            ),
            _response("Recovered from the tool error."),
        ]
    )

    result = _runner(tmp_path, model).run()

    assert result.status == "completed"
    feedback = [message for message in model.requests[1] if message["role"] == "tool"]
    assert "Invalid JSON" in feedback[0]["content"]


def test_max_steps_stops_infinite_tool_loop(tmp_path: Path) -> None:
    responses = [
        _response("", (f"c{i}", "list_files", {})) for i in range(3)
    ]
    result = _runner(tmp_path, FakeModel(responses), max_steps=3).run()
    assert result.status == "max_steps"
    assert result.steps == 3


def test_verification_guard_requests_execution_before_final(tmp_path: Path) -> None:
    (tmp_path / "check.py").write_text("assert True\n", encoding="utf-8")
    model = FakeModel(
        [
            _response(
                "",
                (
                    "e",
                    "edit_file",
                    {"path": "check.py", "old_text": "True", "new_text": "1 == 1"},
                ),
            ),
            _response("I changed the file."),
            _response(
                "",
                (
                    "v",
                    "run_command",
                    {"argv": [sys.executable, "check.py"]},
                ),
            ),
            _response("Changed and verified."),
        ]
    )

    result = _runner(tmp_path, model).run()

    assert result.status == "completed"
    assert result.verification_observed
    guard_messages = [
        message
        for message in model.requests[2]
        if message["role"] == "user" and "not verified" in message["content"]
    ]
    assert len(guard_messages) == 1


def test_verification_guard_reminds_once_when_verification_is_unavailable(
    tmp_path: Path,
) -> None:
    model = FakeModel(
        [
            _response(
                "",
                ("w", "write_file", {"path": "notes.txt", "content": "documentation"}),
            ),
            _response("Done."),
            _response("No meaningful automated verification exists for this text-only change."),
        ]
    )

    result = _runner(tmp_path, model).run()

    assert result.status == "completed"
    assert not result.verification_observed
    assert result.steps == 3
