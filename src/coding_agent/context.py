"""Conversation context with complete-block pruning."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from typing import Any, Iterable


JsonObject = dict[str, Any]


@dataclass(frozen=True, slots=True)
class ContextBlock:
    """Messages that must be retained or pruned as one protocol unit."""

    messages: tuple[JsonObject, ...]

    @property
    def approximate_size(self) -> int:
        return len(json.dumps(self.messages, ensure_ascii=False, default=str))


class ContextManager:
    """Keep stable instructions and recent complete interaction blocks."""

    def __init__(
        self,
        *,
        system_prompt: str,
        original_task: str,
        soft_budget: int = 120_000,
        min_recent_blocks: int = 2,
    ) -> None:
        self._stable = (
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": original_task},
        )
        self._blocks: list[ContextBlock] = []
        self.soft_budget = soft_budget
        self.min_recent_blocks = max(1, min_recent_blocks)
        self.pruned_blocks = 0

    def add_interaction(
        self, assistant_message: JsonObject, tool_messages: Iterable[JsonObject]
    ) -> None:
        tools = tuple(deepcopy(tuple(tool_messages)))
        if not tools:
            raise ValueError("A tool interaction block requires at least one tool result")
        self._blocks.append(
            ContextBlock(messages=(deepcopy(assistant_message), *tools))
        )
        self._prune()

    def add_feedback(self, assistant_message: JsonObject, feedback: str) -> None:
        self._blocks.append(
            ContextBlock(
                messages=(
                    deepcopy(assistant_message),
                    {"role": "user", "content": feedback},
                )
            )
        )
        self._prune()

    def messages(self) -> list[JsonObject]:
        result = [deepcopy(message) for message in self._stable]
        for block in self._blocks:
            result.extend(deepcopy(block.messages))
        return result

    @property
    def block_count(self) -> int:
        return len(self._blocks)

    def _prune(self) -> None:
        while len(self._blocks) > self.min_recent_blocks and self._size() > self.soft_budget:
            self._blocks.pop(0)
            self.pruned_blocks += 1

    def _size(self) -> int:
        stable_size = len(json.dumps(self._stable, ensure_ascii=False))
        return stable_size + sum(block.approximate_size for block in self._blocks)

