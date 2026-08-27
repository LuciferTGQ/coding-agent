from __future__ import annotations

from coding_agent.context import ContextManager


def _assistant(call_id: str, fill: str) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "reasoning_content": fill,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": "demo", "arguments": "{}"},
            }
        ],
    }


def _tool(call_id: str, fill: str) -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": fill}


def test_stable_context_is_always_retained() -> None:
    context = ContextManager(system_prompt="system", original_task="task", soft_budget=1)
    assert context.messages() == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "task"},
    ]


def test_pruning_keeps_complete_recent_blocks_and_provider_fields() -> None:
    context = ContextManager(
        system_prompt="system",
        original_task="task",
        soft_budget=900,
        min_recent_blocks=1,
    )
    for number in range(4):
        call_id = f"call-{number}"
        context.add_interaction(
            _assistant(call_id, str(number) * 300),
            [_tool(call_id, str(number) * 300)],
        )

    messages = context.messages()
    assert context.pruned_blocks > 0
    assert messages[0]["role"] == "system" and messages[1]["role"] == "user"
    dynamic = messages[2:]
    assert len(dynamic) % 2 == 0
    for assistant, tool in zip(dynamic[::2], dynamic[1::2]):
        assert assistant["role"] == "assistant"
        assert "reasoning_content" in assistant
        assert tool["role"] == "tool"
        assert assistant["tool_calls"][0]["id"] == tool["tool_call_id"]


def test_interaction_without_tool_result_is_rejected() -> None:
    context = ContextManager(system_prompt="system", original_task="task")
    try:
        context.add_interaction({"role": "assistant"}, [])
    except ValueError as exc:
        assert "tool result" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_completed_turns_round_trip_and_old_turns_prune_atomically() -> None:
    context = ContextManager(
        system_prompt="system", soft_budget=1100, min_recent_turns=1
    )
    for number in range(3):
        context.start_turn(f"task {number}" + "x" * 300)
        context.finish_turn(
            {"role": "assistant", "content": f"answer {number}" + "y" * 300}
        )

    restored = ContextManager.from_dict(context.to_dict())
    messages = restored.messages()
    assert restored.pruned_turns > 0
    assert messages[0] == {"role": "system", "content": "system"}
    assert messages[-2]["role"] == "user"
    assert messages[-1]["role"] == "assistant"
    assert restored.turn_count == 1
