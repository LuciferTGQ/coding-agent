"""Small tool and result abstractions."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
from typing import Any, Callable


def truncate_text(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    marker = f"\n... [output truncated: {len(text) - limit} characters omitted] ...\n"
    available = max(0, limit - len(marker))
    head = available // 2
    tail = available - head
    return text[:head] + marker + (text[-tail:] if tail else ""), True


@dataclass(frozen=True, slots=True)
class ToolResult:
    ok: bool
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    truncated: bool = False
    changed: bool = False
    verification: bool = False

    @classmethod
    def success(cls, message: str, **kwargs: Any) -> "ToolResult":
        return cls(ok=True, message=message, **kwargs)

    @classmethod
    def failure(cls, error: str, **kwargs: Any) -> "ToolResult":
        return cls(ok=False, message=error, error=error, **kwargs)

    def bounded(self, limit: int) -> "ToolResult":
        message, clipped = truncate_text(self.message, limit)
        return replace(self, message=message, truncated=self.truncated or clipped)

    def to_model_text(self) -> str:
        payload: dict[str, Any] = {
            "ok": self.ok,
            "message": self.message,
        }
        if self.data:
            payload["data"] = self.data
        if self.error:
            payload["error"] = self.error
        if self.truncated:
            payload["truncated"] = True
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., ToolResult]

    def definition(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

