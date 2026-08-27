"""Multi-turn conversation context with protocol-safe pruning."""

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

    def to_dict(self) -> JsonObject:
        return {"messages": deepcopy(list(self.messages))}

    @classmethod
    def from_dict(cls, payload: JsonObject) -> "ContextBlock":
        messages = payload.get("messages")
        if not isinstance(messages, list) or not all(isinstance(item, dict) for item in messages):
            raise ValueError("Invalid serialized context block")
        return cls(tuple(deepcopy(messages)))


@dataclass(slots=True)
class ContextTurn:
    """One user request and all model/tool messages produced while handling it."""

    blocks: list[ContextBlock]
    complete: bool = False

    @property
    def approximate_size(self) -> int:
        return sum(block.approximate_size for block in self.blocks)

    def to_dict(self) -> JsonObject:
        return {"blocks": [block.to_dict() for block in self.blocks], "complete": self.complete}

    @classmethod
    def from_dict(cls, payload: JsonObject) -> "ContextTurn":
        raw_blocks = payload.get("blocks")
        if not isinstance(raw_blocks, list):
            raise ValueError("Invalid serialized context turn")
        blocks = [ContextBlock.from_dict(item) for item in raw_blocks if isinstance(item, dict)]
        if not blocks or blocks[0].messages[0].get("role") != "user":
            raise ValueError("A context turn must begin with a user message")
        return cls(blocks=blocks, complete=bool(payload.get("complete", False)))


class ContextManager:
    """Keep recent model context without splitting tool-call protocol blocks."""

    def __init__(
        self,
        *,
        system_prompt: str,
        original_task: str | None = None,
        soft_budget: int = 120_000,
        min_recent_blocks: int = 2,
        min_recent_turns: int = 1,
    ) -> None:
        self._system = {"role": "system", "content": system_prompt}
        self._turns: list[ContextTurn] = []
        self.soft_budget = soft_budget
        self.min_recent_blocks = max(1, min_recent_blocks)
        self.min_recent_turns = max(1, min_recent_turns)
        self.pruned_blocks = 0
        self.pruned_turns = 0
        if original_task is not None:
            self.start_turn(original_task)

    def start_turn(self, user_content: str) -> None:
        if self._turns and not self._turns[-1].complete:
            raise RuntimeError("Cannot start a new turn before the current turn is complete")
        if not user_content.strip():
            raise ValueError("User content must not be empty")
        self._turns.append(ContextTurn([ContextBlock(({"role": "user", "content": user_content},))]))
        self._prune()

    def add_interaction(
        self, assistant_message: JsonObject, tool_messages: Iterable[JsonObject]
    ) -> None:
        tools = tuple(deepcopy(tuple(tool_messages)))
        if not tools:
            raise ValueError("A tool interaction block requires at least one tool result")
        self._active_turn().blocks.append(ContextBlock((deepcopy(assistant_message), *tools)))
        self._prune()

    def add_feedback(self, assistant_message: JsonObject, feedback: str) -> None:
        self._active_turn().blocks.append(
            ContextBlock((deepcopy(assistant_message), {"role": "user", "content": feedback}))
        )
        self._prune()

    def finish_turn(self, assistant_message: JsonObject) -> None:
        turn = self._active_turn()
        turn.blocks.append(ContextBlock((deepcopy(assistant_message),)))
        turn.complete = True
        self._prune()

    def abandon_turn(self, assistant_message: JsonObject | None = None) -> None:
        """Close an interrupted turn while retaining all valid accumulated blocks."""

        turn = self._active_turn()
        if assistant_message is not None:
            turn.blocks.append(ContextBlock((deepcopy(assistant_message),)))
        turn.complete = True
        self._prune()

    def messages(self) -> list[JsonObject]:
        result = [deepcopy(self._system)]
        for turn in self._turns:
            for block in turn.blocks:
                result.extend(deepcopy(block.messages))
        return result

    def to_dict(self) -> JsonObject:
        return {
            "version": 1,
            "system": deepcopy(self._system),
            "turns": [turn.to_dict() for turn in self._turns],
            "soft_budget": self.soft_budget,
            "min_recent_blocks": self.min_recent_blocks,
            "min_recent_turns": self.min_recent_turns,
            "pruned_blocks": self.pruned_blocks,
            "pruned_turns": self.pruned_turns,
        }

    @classmethod
    def from_dict(cls, payload: JsonObject) -> "ContextManager":
        system = payload.get("system")
        if not isinstance(system, dict) or system.get("role") != "system":
            raise ValueError("Invalid serialized system context")
        context = cls(
            system_prompt=str(system.get("content", "")),
            soft_budget=int(payload.get("soft_budget", 120_000)),
            min_recent_blocks=int(payload.get("min_recent_blocks", 2)),
            min_recent_turns=int(payload.get("min_recent_turns", 1)),
        )
        raw_turns = payload.get("turns", [])
        if not isinstance(raw_turns, list):
            raise ValueError("Invalid serialized context turns")
        context._turns = [ContextTurn.from_dict(item) for item in raw_turns if isinstance(item, dict)]
        context.pruned_blocks = int(payload.get("pruned_blocks", 0))
        context.pruned_turns = int(payload.get("pruned_turns", 0))
        return context

    @property
    def block_count(self) -> int:
        return sum(max(0, len(turn.blocks) - 1) for turn in self._turns)

    @property
    def turn_count(self) -> int:
        return len(self._turns)

    @property
    def has_active_turn(self) -> bool:
        return bool(self._turns and not self._turns[-1].complete)

    def _active_turn(self) -> ContextTurn:
        if not self._turns or self._turns[-1].complete:
            raise RuntimeError("No active user turn")
        return self._turns[-1]

    def _prune(self) -> None:
        while self._size() > self.soft_budget:
            complete_indexes = [i for i, turn in enumerate(self._turns) if turn.complete]
            if len(complete_indexes) <= self.min_recent_turns:
                break
            self._turns.pop(complete_indexes[0])
            self.pruned_turns += 1

        if self._turns and not self._turns[-1].complete:
            active = self._turns[-1]
            while self._size() > self.soft_budget and len(active.blocks) - 1 > self.min_recent_blocks:
                active.blocks.pop(1)
                self.pruned_blocks += 1

    def _size(self) -> int:
        return len(json.dumps(self._system, ensure_ascii=False)) + sum(
            turn.approximate_size for turn in self._turns
        )
