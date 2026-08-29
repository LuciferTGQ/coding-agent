from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
from threading import Event, Lock
from typing import Sequence

import pytest

from coding_agent.agent import AgentRunner
from coding_agent.context import ContextManager
from coding_agent.llm import AssistantResponse, ToolCall
from coding_agent.subagents import DelegateTaskService
from coding_agent.tools import ToolRegistry, create_command_tool, create_file_tools
from coding_agent.tools.base import Tool, ToolResult
from coding_agent.workspace import Workspace


def _response(content: str = "", *calls: tuple[str, str, dict]) -> AssistantResponse:
    tool_calls = tuple(
        ToolCall(call_id, name, json.dumps(arguments))
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


class ScriptedModel:
    def __init__(self, responses: list[AssistantResponse]) -> None:
        self.responses = responses
        self.requests: list[tuple[list[dict], list[dict]]] = []

    def complete(self, *, messages: Sequence[dict], tools: Sequence[dict]) -> AssistantResponse:
        self.requests.append((list(messages), list(tools)))
        return self.responses.pop(0)


def _task_tool(handler) -> Tool:
    return Tool(
        "delegate_task",
        "delegate",
        {
            "type": "object",
            "properties": {"task": {"type": "string"}},
            "required": ["task"],
            "additionalProperties": False,
        },
        handler,
    )


def _service(
    workspace: Path,
    model_factory,
    should_cancel=lambda: False,
    *,
    max_steps: int = 8,
    max_delegations: int = 8,
) -> DelegateTaskService:
    return DelegateTaskService(
        workspace=workspace,
        model_factory=model_factory,
        should_cancel=should_cancel,
        max_steps=max_steps,
        max_delegations=max_delegations,
        max_read_lines=200,
        max_search_results=100,
        tool_output_limit=12_000,
    )


def test_child_uses_fresh_context_and_only_read_only_tools(tmp_path: Path) -> None:
    (tmp_path / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    child = ScriptedModel(
        [
            _response("", ("read", "read_file", {"path": "module.py"})),
            _response("Relevant Files: module.py\nEvidence: VALUE is 1"),
        ]
    )
    service = _service(tmp_path, lambda: child)

    result = service.delegate("Inspect the value definition. Parent secret history must not appear.")

    assert result.ok
    assert "VALUE is 1" in result.message
    first_messages, first_tools = child.requests[0]
    assert [item["function"]["name"] for item in first_tools] == [
        "list_files",
        "read_file",
        "search_text",
    ]
    assert [message["role"] for message in first_messages] == ["system", "user"]
    assert "Parent secret history" in first_messages[1]["content"]
    assert "write_file" not in str(first_tools)
    assert "edit_file" not in str(first_tools)
    assert "run_command" not in str(first_tools)
    assert "delegate_task" not in str(first_tools)


def test_child_trace_is_reduced_to_one_parent_tool_result(tmp_path: Path) -> None:
    (tmp_path / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    child = ScriptedModel(
        [
            _response("", ("child-read", "read_file", {"path": "module.py"})),
            _response("module.py defines VALUE = 1"),
        ]
    )
    service = _service(tmp_path, lambda: child)
    parent = ScriptedModel(
        [
            _response("", ("delegate", "delegate_task", {"task": "Inspect module.py"})),
            _response("I used the condensed findings."),
        ]
    )
    context = ContextManager(system_prompt="parent", original_task="Main history")
    runner = AgentRunner(
        model=parent,
        tools=ToolRegistry([service.tool()]),
        context=context,
    )

    result = runner.run()

    assert result.status == "completed"
    parent_second_messages = parent.requests[1][0]
    parent_tools = [message for message in parent_second_messages if message["role"] == "tool"]
    assert len(parent_tools) == 1
    assert parent_tools[0]["tool_call_id"] == "delegate"
    assert "module.py defines VALUE = 1" in parent_tools[0]["content"]
    assert "child-read" not in str(parent_second_messages)
    assert "Main history" not in str(child.requests)


def test_each_child_creates_a_distinct_model_and_fresh_context(tmp_path: Path) -> None:
    created = []
    lock = Lock()

    def factory() -> ScriptedModel:
        model = ScriptedModel([_response("independent findings")])
        with lock:
            created.append(model)
        return model

    service = _service(tmp_path, factory)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(service.delegate, ["task A", "task B"]))

    assert all(result.ok for result in results)
    assert len(created) == 2
    assert created[0] is not created[1]
    user_messages = [
        model.requests[0][0][1]["content"]
        for model in created
    ]
    assert sorted(user_messages) == ["task A", "task B"]


@pytest.mark.parametrize("child_count", [2, 3, 4])
def test_pure_delegation_batches_really_overlap_and_preserve_call_order(
    tmp_path: Path, child_count: int
) -> None:
    arrived = 0
    active = 0
    maximum_active = 0
    all_arrived = Event()
    release = Event()
    lock = Lock()

    def delegate(task: str) -> ToolResult:
        nonlocal arrived, active, maximum_active
        with lock:
            arrived += 1
            active += 1
            maximum_active = max(maximum_active, active)
            if arrived == child_count:
                all_arrived.set()
        assert release.wait(2)
        with lock:
            active -= 1
        return ToolResult.success(f"findings {task}")

    calls = tuple(
        (f"call-{index}", "delegate_task", {"task": str(index)})
        for index in range(child_count)
    )
    model = ScriptedModel([_response("", *calls), _response("combined")])
    runner = AgentRunner(
        model=model,
        tools=ToolRegistry([_task_tool(delegate)]),
        context=ContextManager(system_prompt="system", original_task="investigate"),
        parallel_tool_names=frozenset({"delegate_task"}),
        max_parallel_tools=10,
    )

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(runner.run)
        assert all_arrived.wait(2)
        assert maximum_active == child_count
        release.set()
        assert future.result().status == "completed"

    results = [message for message in model.requests[1][0] if message["role"] == "tool"]
    assert [message["tool_call_id"] for message in results] == [
        f"call-{index}" for index in range(child_count)
    ]


def test_parallel_delegation_never_exceeds_four_workers(tmp_path: Path) -> None:
    started = 0
    active = 0
    maximum_active = 0
    first_four_started = Event()
    fifth_started = Event()
    release = Event()
    lock = Lock()

    def delegate(task: str) -> ToolResult:
        nonlocal started, active, maximum_active
        with lock:
            started += 1
            active += 1
            maximum_active = max(maximum_active, active)
            if started == 4:
                first_four_started.set()
            if started == 5:
                fifth_started.set()
        assert release.wait(2)
        with lock:
            active -= 1
        return ToolResult.success(task)

    calls = tuple(
        (f"call-{index}", "delegate_task", {"task": str(index)}) for index in range(5)
    )
    runner = AgentRunner(
        model=ScriptedModel([_response("", *calls), _response("done")]),
        tools=ToolRegistry([_task_tool(delegate)]),
        context=ContextManager(system_prompt="system", original_task="investigate"),
        parallel_tool_names=frozenset({"delegate_task"}),
        max_parallel_tools=10,
    )

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(runner.run)
        assert first_four_started.wait(2)
        assert not fifth_started.is_set()
        assert maximum_active == 4
        release.set()
        assert future.result().status == "completed"

    assert fifth_started.is_set()
    assert maximum_active == 4


def test_one_child_failure_keeps_sibling_results(tmp_path: Path) -> None:
    all_started = Event()
    release = Event()
    lock = Lock()
    started = 0

    def delegate(task: str) -> ToolResult:
        nonlocal started
        with lock:
            started += 1
            if started == 3:
                all_started.set()
        assert release.wait(2)
        return (
            ToolResult.failure("child failed")
            if task == "fail"
            else ToolResult.success(f"success {task}")
        )

    calls = (
        ("a", "delegate_task", {"task": "one"}),
        ("b", "delegate_task", {"task": "fail"}),
        ("c", "delegate_task", {"task": "three"}),
    )
    model = ScriptedModel([_response("", *calls), _response("combined")])
    runner = AgentRunner(
        model=model,
        tools=ToolRegistry([_task_tool(delegate)]),
        context=ContextManager(system_prompt="system", original_task="investigate"),
        parallel_tool_names=frozenset({"delegate_task"}),
    )

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(runner.run)
        assert all_started.wait(2)
        release.set()
        assert future.result().status == "completed"

    results = [message for message in model.requests[1][0] if message["role"] == "tool"]
    assert [message["tool_call_id"] for message in results] == ["a", "b", "c"]
    assert '"ok":true' in results[0]["content"]
    assert '"ok":false' in results[1]["content"]
    assert '"ok":true' in results[2]["content"]


def test_mixed_tool_batch_stays_sequential(tmp_path: Path) -> None:
    order = []

    def delegate(task: str) -> ToolResult:
        order.append("delegate")
        return ToolResult.success(task)

    def inspect() -> ToolResult:
        assert order == ["delegate"]
        order.append("inspect")
        return ToolResult.success("inspected")

    inspect_tool = Tool(
        "inspect",
        "inspect",
        {"type": "object", "properties": {}, "additionalProperties": False},
        inspect,
    )
    model = ScriptedModel(
        [
            _response(
                "",
                ("a", "delegate_task", {"task": "one"}),
                ("b", "inspect", {}),
            ),
            _response("done"),
        ]
    )
    runner = AgentRunner(
        model=model,
        tools=ToolRegistry([_task_tool(delegate), inspect_tool]),
        context=ContextManager(system_prompt="system", original_task="mixed"),
        parallel_tool_names=frozenset({"delegate_task"}),
    )

    assert runner.run().status == "completed"
    assert order == ["delegate", "inspect"]


def test_parent_cancellation_propagates_after_child_model_boundary(tmp_path: Path) -> None:
    entered_model = Event()
    release_model = Event()
    cancel = Event()

    class BoundaryModel:
        def complete(self, *, messages: Sequence[dict], tools: Sequence[dict]) -> AssistantResponse:
            entered_model.set()
            assert release_model.wait(2)
            return _response("", ("read", "list_files", {}))

    service = _service(tmp_path, lambda: BoundaryModel(), cancel.is_set)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(service.delegate, "Inspect the project")
        assert entered_model.wait(2)
        cancel.set()
        release_model.set()
        result = future.result()

    assert not result.ok
    assert "cancelled" in result.message


def test_child_max_steps_and_parent_delegation_limit_return_tool_feedback(
    tmp_path: Path,
) -> None:
    class LoopingModel:
        def __init__(self) -> None:
            self.call = 0

        def complete(self, *, messages: Sequence[dict], tools: Sequence[dict]) -> AssistantResponse:
            self.call += 1
            return _response("", (f"read-{self.call}", "list_files", {}))

    limited_steps = _service(tmp_path, lambda: LoopingModel(), max_steps=2)
    max_step_result = limited_steps.delegate("keep looking")
    assert not max_step_result.ok
    assert "max_steps" in max_step_result.message

    final_model = lambda: ScriptedModel([_response("done")])
    limited_count = _service(
        tmp_path,
        lambda: final_model(),
        max_delegations=2,
    )
    assert limited_count.delegate("one").ok
    assert limited_count.delegate("two").ok
    third = limited_count.delegate("three")
    assert not third.ok
    assert "limit reached" in third.message


def test_main_can_modify_and_verify_after_child_findings(tmp_path: Path) -> None:
    child = ScriptedModel([_response("value.py should contain VALUE = 2")])
    service = _service(tmp_path, lambda: child)
    model = ScriptedModel(
        [
            _response("", ("d", "delegate_task", {"task": "Choose a value"})),
            _response(
                "",
                ("w", "write_file", {"path": "value.py", "content": "VALUE = 2\n"}),
            ),
            _response(
                "",
                (
                    "v",
                    "run_command",
                    {"argv": [sys.executable, "value.py"]},
                ),
            ),
            _response("Implemented and verified."),
        ]
    )
    workspace = Workspace(tmp_path)
    registry = ToolRegistry(
        [
            *create_file_tools(workspace),
            create_command_tool(workspace, default_timeout=5),
            service.tool(),
        ]
    )
    result = AgentRunner(
        model=model,
        tools=registry,
        context=ContextManager(system_prompt="system", original_task="implement"),
        parallel_tool_names=frozenset({"delegate_task"}),
    ).run()

    assert result.status == "completed"
    assert result.workspace_changed
    assert result.verification_observed
    assert (tmp_path / "value.py").read_text(encoding="utf-8") == "VALUE = 2\n"
