"""DeepSeek Chat Completions adapter and provider-neutral response types."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Callable, Protocol, Sequence

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    OpenAI,
    PermissionDeniedError,
    RateLimitError,
)


JsonObject = dict[str, Any]


class ModelError(RuntimeError):
    """A sanitized model request or protocol failure."""


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass(frozen=True, slots=True)
class AssistantResponse:
    """Normalized model output plus its replayable provider message."""

    content: str
    tool_calls: tuple[ToolCall, ...]
    provider_message: JsonObject


class ModelClient(Protocol):
    def complete(
        self, *, messages: Sequence[JsonObject], tools: Sequence[JsonObject]
    ) -> AssistantResponse: ...


class DeepSeekChatClient:
    """Thin DeepSeek adapter; orchestration and tool execution live elsewhere."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        reasoning_effort: str = "high",
        thinking_enabled: bool = True,
        request_timeout: float = 120.0,
        max_retries: int = 2,
        client: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._reasoning_effort = reasoning_effort
        self._thinking_enabled = thinking_enabled
        self._max_retries = max(0, max_retries)
        self._sleep = sleep
        self._client = client or OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=request_timeout,
            max_retries=0,
        )

    def complete(
        self, *, messages: Sequence[JsonObject], tools: Sequence[JsonObject]
    ) -> AssistantResponse:
        request: JsonObject = {
            "model": self._model,
            "messages": list(messages),
            "extra_body": {
                "thinking": {"type": "enabled" if self._thinking_enabled else "disabled"}
            },
        }
        if tools:
            request["tools"] = list(tools)
        if self._thinking_enabled:
            request["reasoning_effort"] = self._reasoning_effort

        for attempt in range(self._max_retries + 1):
            try:
                completion = self._client.chat.completions.create(**request)
                return self._normalize(completion)
            except Exception as exc:  # SDK errors share no single useful base for retry policy.
                if attempt < self._max_retries and self._is_retryable(exc):
                    self._sleep(min(2**attempt, 4))
                    continue
                message = str(exc).replace(self._api_key, "[REDACTED]")
                raise ModelError(
                    f"DeepSeek request failed ({type(exc).__name__}): {message}"
                ) from exc

        raise AssertionError("unreachable")

    def _normalize(self, completion: Any) -> AssistantResponse:
        choices = getattr(completion, "choices", None)
        if not choices:
            raise ModelError("DeepSeek returned no completion choices")
        message = getattr(choices[0], "message", None)
        if message is None:
            raise ModelError("DeepSeek returned a choice without an assistant message")

        content_value = getattr(message, "content", None)
        content = content_value if isinstance(content_value, str) else ""
        raw_calls = getattr(message, "tool_calls", None) or []
        calls: list[ToolCall] = []
        replay_calls: list[JsonObject] = []
        for raw_call in raw_calls:
            function = getattr(raw_call, "function", None)
            call_id = getattr(raw_call, "id", None)
            name = getattr(function, "name", None)
            arguments = getattr(function, "arguments", None)
            if not all(isinstance(value, str) for value in (call_id, name, arguments)):
                raise ModelError("DeepSeek returned a malformed tool call")
            calls.append(ToolCall(id=call_id, name=name, arguments=arguments))
            replay_calls.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": arguments},
                }
            )

        provider_message: JsonObject = {
            "role": "assistant",
            "content": content_value,
        }
        # DeepSeek requires this exact field to be replayed after thinking-mode
        # tool calls. It stays provider-specific and is never displayed.
        if hasattr(message, "reasoning_content"):
            provider_message["reasoning_content"] = getattr(message, "reasoning_content")
        if replay_calls:
            provider_message["tool_calls"] = replay_calls

        if not content and not calls:
            raise ModelError("DeepSeek returned neither content nor tool calls")
        return AssistantResponse(
            content=content,
            tool_calls=tuple(calls),
            provider_message=provider_message,
        )

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        if isinstance(
            exc,
            (AuthenticationError, BadRequestError, PermissionDeniedError, NotFoundError),
        ):
            return False
        if isinstance(
            exc,
            (RateLimitError, APITimeoutError, APIConnectionError, InternalServerError),
        ):
            return True
        if isinstance(exc, APIStatusError):
            return exc.status_code >= 500
        return False

