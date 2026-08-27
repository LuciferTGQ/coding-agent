from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from coding_agent.llm import DeepSeekChatClient, ModelError


def _response(*, content: str | None, reasoning: str | None, calls: list[Any]) -> Any:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=content,
                    reasoning_content=reasoning,
                    tool_calls=calls,
                )
            )
        ]
    )


def _call(call_id: str, name: str, arguments: str) -> Any:
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


class FakeCompletions:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = outcomes
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _client(outcomes: list[Any], **kwargs: Any) -> tuple[DeepSeekChatClient, FakeCompletions]:
    completions = FakeCompletions(outcomes)
    sdk = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return (
        DeepSeekChatClient(
            api_key="test-key",
            base_url="https://example.invalid",
            model="deepseek-v4-flash",
            client=sdk,
            sleep=lambda _: None,
            **kwargs,
        ),
        completions,
    )


def test_normalizes_calls_and_preserves_reasoning_for_replay() -> None:
    model, completions = _client(
        [_response(content=None, reasoning="private reasoning", calls=[_call("c1", "read", '{"path":"x"}')])]
    )
    messages = [{"role": "user", "content": "inspect"}]
    tools = [{"type": "function", "function": {"name": "read"}}]

    result = model.complete(messages=messages, tools=tools)

    assert result.content == ""
    assert result.tool_calls[0].name == "read"
    assert result.provider_message["reasoning_content"] == "private reasoning"
    assert result.provider_message["tool_calls"][0]["id"] == "c1"
    request = completions.requests[0]
    assert request["reasoning_effort"] == "high"
    assert request["extra_body"] == {"thinking": {"type": "enabled"}}


def test_multiple_tool_calls_keep_order() -> None:
    model, _ = _client(
        [
            _response(
                content="",
                reasoning="r",
                calls=[_call("c1", "first", "{}"), _call("c2", "second", "{}")],
            )
        ]
    )

    result = model.complete(messages=[], tools=[])

    assert [call.name for call in result.tool_calls] == ["first", "second"]


def test_no_thinking_omits_effort() -> None:
    model, completions = _client(
        [_response(content="done", reasoning=None, calls=[])], thinking_enabled=False
    )

    model.complete(messages=[], tools=[])

    request = completions.requests[0]
    assert "reasoning_effort" not in request
    assert request["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "tools" not in request


def test_retryable_connection_error_is_retried() -> None:
    from openai import APIConnectionError

    model, completions = _client(
        [APIConnectionError(request=SimpleNamespace()), _response(content="ok", reasoning="r", calls=[])],
        max_retries=1,
    )

    result = model.complete(messages=[], tools=[])

    assert result.content == "ok"
    assert len(completions.requests) == 2


def test_unknown_error_is_not_retried_and_key_is_redacted() -> None:
    model, completions = _client([RuntimeError("leaked test-key")], max_retries=2)

    with pytest.raises(ModelError, match="REDACTED") as caught:
        model.complete(messages=[], tools=[])

    assert "test-key" not in str(caught.value)
    assert len(completions.requests) == 1


def test_empty_response_is_protocol_error() -> None:
    model, _ = _client([_response(content=None, reasoning="r", calls=[])])

    with pytest.raises(ModelError, match="neither content nor tool calls"):
        model.complete(messages=[], tools=[])

