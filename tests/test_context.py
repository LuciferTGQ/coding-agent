from __future__ import annotations

import json

from coding_agent.context import ContextManager


def _assistant(call_id: str, name: str = "read_file", fill: str = "private") -> dict:
    return {
        "role": "assistant",
        "content": None,
        "reasoning_content": fill,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": "{}"},
            }
        ],
    }


def _tool(call_id: str, fill: str) -> dict:
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "content": json.dumps({"ok": True, "message": fill}),
    }


def _complete_tool_turn(
    context: ContextManager,
    number: int,
    *,
    name: str = "read_file",
    tool_size: int = 40,
) -> None:
    call_id = f"call-{number}"
    context.start_turn(f"task {number}")
    context.add_interaction(
        _assistant(call_id, name),
        [_tool(call_id, f"observation-{number}-" + "x" * tool_size)],
    )
    context.finish_turn(
        {"role": "assistant", "content": f"answer {number}", "reasoning_content": "done"}
    )


def _assert_tool_pairing(messages: list[dict]) -> None:
    calls: dict[str, int] = {}
    results: dict[str, int] = {}
    for index, message in enumerate(messages):
        if message.get("role") == "assistant":
            for call in message.get("tool_calls", []):
                calls[call["id"]] = index
        elif message.get("role") == "tool":
            results[message["tool_call_id"]] = index
    assert calls.keys() == results.keys()
    assert all(calls[call_id] < results[call_id] for call_id in calls)


def test_active_turn_is_retained_even_when_it_exceeds_soft_character_budget() -> None:
    context = ContextManager(
        system_prompt="system", original_task="task", soft_budget_chars=1
    )

    assert context.compact(lambda *_: "unused") is False
    assert context.messages() == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "task"},
    ]


def test_old_large_read_only_results_compress_before_summary_and_keep_pairing() -> None:
    context = ContextManager(
        system_prompt="system",
        soft_budget_chars=1,
        recent_completed_turns=2,
        tool_result_compression_chars=100,
    )
    for number in range(4):
        _complete_tool_turn(context, number, tool_size=500)

    context.compact()
    messages = context.messages()
    tool_messages = [message for message in messages if message.get("role") == "tool"]

    assert context.compressed_tool_results == 2
    assert json.loads(tool_messages[0]["content"])["compacted"] is True
    assert json.loads(tool_messages[1]["content"])["compacted"] is True
    assert "observation-2" in tool_messages[2]["content"]
    assert "observation-3" in tool_messages[3]["content"]
    _assert_tool_pairing(messages)
    assert all(
        "reasoning_content" in message
        for message in messages
        if message.get("tool_calls")
    )


def test_write_results_are_not_treated_as_low_value_read_output() -> None:
    context = ContextManager(
        system_prompt="system",
        soft_budget_chars=1,
        recent_completed_turns=1,
        tool_result_compression_chars=10,
    )
    _complete_tool_turn(context, 0, name="write_file", tool_size=500)
    _complete_tool_turn(context, 1, tool_size=20)

    context.compact()

    write_result = next(
        message for message in context.messages() if message.get("tool_call_id") == "call-0"
    )
    assert "observation-0" in write_result["content"]
    assert context.compressed_tool_results == 0


def test_structured_summary_replaces_only_old_completed_turns() -> None:
    context = ContextManager(
        system_prompt="system",
        soft_budget_chars=500,
        recent_completed_turns=2,
    )
    for number in range(4):
        _complete_tool_turn(context, number, tool_size=100)
    context.start_turn("current active task")
    calls = []

    def summarize(previous, turns) -> str:
        calls.append((previous, turns))
        return "## Goal\nContinue the project\n\n## Remaining Work\nCurrent task"

    assert context.compact(summarize) is True
    messages = context.messages()

    assert len(calls) == 1
    assert calls[0][0] is None
    assert len(calls[0][1]) == 2
    assert context.summary is not None
    assert context.compaction_count == 1
    assert context.summarized_turns == 2
    assert context.turn_count == 3
    assert "task 0" not in str(messages)
    assert "task 1" not in str(messages)
    assert "task 2" in str(messages) and "task 3" in str(messages)
    assert messages[-1] == {"role": "user", "content": "current active task"}
    assert "<working_memory>" in messages[0]["content"]
    assert "current workspace" in messages[0]["content"]
    _assert_tool_pairing(messages)


def test_below_threshold_does_not_call_summarizer() -> None:
    context = ContextManager(system_prompt="system", soft_budget_chars=10_000)
    for number in range(3):
        _complete_tool_turn(context, number)
    calls = []

    assert context.compact(lambda *args: calls.append(args) or "summary") is False
    assert calls == []
    assert context.summary is None


def test_rolling_summary_uses_previous_summary_and_only_newly_aged_turns() -> None:
    context = ContextManager(
        system_prompt="system",
        soft_budget_chars=1,
        recent_completed_turns=1,
        tool_result_compression_chars=10_000,
    )
    for number in range(3):
        _complete_tool_turn(context, number)
    inputs = []

    def summarize_a(previous, turns) -> str:
        inputs.append((previous, turns))
        return "Summary A"

    context.compact(summarize_a)
    assert context.summary == "Summary A"
    assert context.summarized_turns == 2

    _complete_tool_turn(context, 3)
    _complete_tool_turn(context, 4)

    def summarize_b(previous, turns) -> str:
        inputs.append((previous, turns))
        return "Summary B"

    context.compact(summarize_b)

    assert inputs[1][0] == "Summary A"
    assert len(inputs[1][1]) == 2
    assert "task 0" not in str(inputs[1][1])
    assert "task 2" in str(inputs[1][1])
    assert "task 3" in str(inputs[1][1])
    assert context.summary == "Summary B"
    assert context.compaction_count == 2
    assert context.summarized_turns == 4
    assert "task 4" in str(context.messages())


def test_summary_failure_keeps_valid_context_and_is_not_retried_for_same_candidates() -> None:
    context = ContextManager(
        system_prompt="system", soft_budget_chars=1, recent_completed_turns=1
    )
    for number in range(3):
        _complete_tool_turn(context, number)
    before = context.to_dict()
    calls = 0

    def fail(*_args) -> str:
        nonlocal calls
        calls += 1
        raise RuntimeError("rate limited")

    assert context.compact(fail) is False
    assert context.compact(fail) is False

    after = context.to_dict()
    assert calls == 1
    assert context.summary_failures == 1
    assert after["turns"] == before["turns"]
    assert after["summary"] == before["summary"]


def test_summary_and_statistics_round_trip_without_regeneration() -> None:
    context = ContextManager(
        system_prompt="system", soft_budget_chars=1, recent_completed_turns=1
    )
    for number in range(3):
        _complete_tool_turn(context, number)
    context.compact(lambda *_: "Persisted summary")

    restored = ContextManager.from_dict(context.to_dict())

    assert restored.summary == "Persisted summary"
    assert restored.compaction_count == 1
    assert restored.summarized_turns == 2
    assert "Persisted summary" in restored.messages()[0]["content"]


def test_version_one_model_context_remains_loadable() -> None:
    old_payload = {
        "version": 1,
        "system": {"role": "system", "content": "old system"},
        "turns": [
            {
                "blocks": [
                    {"messages": [{"role": "user", "content": "old request"}]},
                    {"messages": [{"role": "assistant", "content": "old answer"}]},
                ],
                "complete": True,
            }
        ],
        "soft_budget": 4567,
        "min_recent_turns": 1,
        "pruned_turns": 3,
    }

    restored = ContextManager.from_dict(old_payload)

    assert restored.soft_budget_chars == 4567
    assert restored.recent_completed_turns == 2
    assert restored.summarized_turns == 3
    assert restored.messages()[-1]["content"] == "old answer"


def test_interaction_without_tool_result_is_rejected() -> None:
    context = ContextManager(system_prompt="system", original_task="task")
    try:
        context.add_interaction({"role": "assistant"}, [])
    except ValueError as exc:
        assert "tool result" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_system_prompt_can_change_without_disturbing_summary_or_turns() -> None:
    context = ContextManager(system_prompt="English", original_task="inspect")
    context.summary = "Existing memory"
    context.set_system_prompt("Chinese")

    assert context.messages()[0]["content"].startswith("Chinese")
    assert "Existing memory" in context.messages()[0]["content"]
    assert context.messages()[1] == {"role": "user", "content": "inspect"}
