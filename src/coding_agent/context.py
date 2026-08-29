"""Multi-turn working context with protocol-safe compaction."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import logging
from typing import Any, Iterable

JsonObject = dict[str, Any]
SummaryCallback = Callable[[str | None, list[JsonObject]], str]
LOGGER = logging.getLogger(__name__)

DEFAULT_SOFT_BUDGET_CHARS = 120_000
DEFAULT_RECENT_COMPLETED_TURNS = 2
DEFAULT_TOOL_RESULT_COMPRESSION_CHARS = 2_000
DEFAULT_SUMMARY_MAX_CHARS = 16_000
COMPRESSIBLE_TOOL_NAMES = frozenset({"list_files", "read_file", "search_text"})
WORKING_MEMORY_HEADER = """

Working memory from older completed turns follows. Treat it as a lossy historical
summary, not as the source of truth. The current workspace and newly observed tool
results are authoritative; re-read, search, or verify details that may have changed.

<working_memory>
{summary}
</working_memory>"""


@dataclass(frozen=True, slots=True)
class ContextBlock:
    """Messages that must be retained or compacted as one protocol unit."""

    messages: tuple[JsonObject, ...]

    @property
    def approximate_size_chars(self) -> int:
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
    def approximate_size_chars(self) -> int:
        return sum(block.approximate_size_chars for block in self.blocks)

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
    """Keep structured working memory plus recent protocol-complete raw turns."""

    def __init__(
        self,
        *,
        system_prompt: str,
        original_task: str | None = None,
        soft_budget_chars: int = DEFAULT_SOFT_BUDGET_CHARS,
        recent_completed_turns: int = DEFAULT_RECENT_COMPLETED_TURNS,
        tool_result_compression_chars: int = DEFAULT_TOOL_RESULT_COMPRESSION_CHARS,
        summary_max_chars: int = DEFAULT_SUMMARY_MAX_CHARS,
    ) -> None:
        self._system = {"role": "system", "content": system_prompt}
        self._turns: list[ContextTurn] = []
        self.summary: str | None = None
        self.soft_budget_chars = max(1, soft_budget_chars)
        self.recent_completed_turns = max(1, recent_completed_turns)
        self.tool_result_compression_chars = max(1, tool_result_compression_chars)
        self.summary_max_chars = max(1, summary_max_chars)
        self.compressed_tool_results = 0
        self.compaction_count = 0
        self.summarized_turns = 0
        self.summary_failures = 0
        self._failed_candidate_fingerprint: str | None = None
        if original_task is not None:
            self.start_turn(original_task)

    def start_turn(self, user_content: str) -> None:
        if self._turns and not self._turns[-1].complete:
            raise RuntimeError("Cannot start a new turn before the current turn is complete")
        if not user_content.strip():
            raise ValueError("User content must not be empty")
        self._turns.append(ContextTurn([ContextBlock(({"role": "user", "content": user_content},))]))

    def set_system_prompt(self, content: str) -> None:
        """Update stable instructions without disturbing memory or conversation turns."""

        self._system = {"role": "system", "content": content}

    def add_interaction(
        self, assistant_message: JsonObject, tool_messages: Iterable[JsonObject]
    ) -> None:
        tools = tuple(deepcopy(tuple(tool_messages)))
        if not tools:
            raise ValueError("A tool interaction block requires at least one tool result")
        self._active_turn().blocks.append(ContextBlock((deepcopy(assistant_message), *tools)))

    def add_feedback(self, assistant_message: JsonObject, feedback: str) -> None:
        self._active_turn().blocks.append(
            ContextBlock((deepcopy(assistant_message), {"role": "user", "content": feedback}))
        )

    def finish_turn(self, assistant_message: JsonObject) -> None:
        turn = self._active_turn()
        turn.blocks.append(ContextBlock((deepcopy(assistant_message),)))
        turn.complete = True

    def abandon_turn(self, assistant_message: JsonObject | None = None) -> None:
        """Close an interrupted turn while retaining all valid accumulated blocks."""

        turn = self._active_turn()
        if assistant_message is not None:
            turn.blocks.append(ContextBlock((deepcopy(assistant_message),)))
        turn.complete = True

    def compact(self, summarizer: SummaryCallback | None = None) -> bool:
        """Compact older context, committing summary replacement only after success."""

        if self.approximate_size_chars <= self.soft_budget_chars:
            return False
        self._compress_old_tool_results()
        if self.approximate_size_chars <= self.soft_budget_chars:
            return True

        candidate_indexes = self._summary_candidate_indexes()
        if not candidate_indexes or summarizer is None:
            return False
        candidates = [self._turns[index].to_dict() for index in candidate_indexes]
        fingerprint = _fingerprint(self.summary, candidates)
        if fingerprint == self._failed_candidate_fingerprint:
            return False
        try:
            replacement = summarizer(self.summary, deepcopy(candidates)).strip()
            if not replacement:
                raise ValueError("Summarizer returned empty working memory")
            if len(replacement) > self.summary_max_chars:
                raise ValueError(
                    f"Summarizer exceeded the {self.summary_max_chars} character limit"
                )
        except Exception as exc:
            self.summary_failures += 1
            self._failed_candidate_fingerprint = fingerprint
            LOGGER.warning("Working-memory summarization failed: %s", exc)
            return False

        remove = set(candidate_indexes)
        self._turns = [turn for index, turn in enumerate(self._turns) if index not in remove]
        self.summary = replacement
        self.compaction_count += 1
        self.summarized_turns += len(candidate_indexes)
        self._failed_candidate_fingerprint = None
        return True

    def messages(self) -> list[JsonObject]:
        system = deepcopy(self._system)
        if self.summary:
            system["content"] = str(system.get("content", "")) + WORKING_MEMORY_HEADER.format(
                summary=self.summary
            )
        result = [system]
        for turn in self._turns:
            for block in turn.blocks:
                result.extend(deepcopy(block.messages))
        return result

    def to_dict(self) -> JsonObject:
        return {
            "version": 2,
            "system": deepcopy(self._system),
            "summary": self.summary,
            "turns": [turn.to_dict() for turn in self._turns],
            "soft_budget_chars": self.soft_budget_chars,
            "recent_completed_turns": self.recent_completed_turns,
            "tool_result_compression_chars": self.tool_result_compression_chars,
            "summary_max_chars": self.summary_max_chars,
            "compressed_tool_results": self.compressed_tool_results,
            "compaction_count": self.compaction_count,
            "summarized_turns": self.summarized_turns,
            "summary_failures": self.summary_failures,
        }

    @classmethod
    def from_dict(cls, payload: JsonObject) -> "ContextManager":
        system = payload.get("system")
        if not isinstance(system, dict) or system.get("role") != "system":
            raise ValueError("Invalid serialized system context")
        context = cls(
            system_prompt=str(system.get("content", "")),
            soft_budget_chars=int(
                payload.get("soft_budget_chars", payload.get("soft_budget", DEFAULT_SOFT_BUDGET_CHARS))
            ),
            recent_completed_turns=int(
                payload.get("recent_completed_turns", DEFAULT_RECENT_COMPLETED_TURNS)
            ),
            tool_result_compression_chars=int(
                payload.get(
                    "tool_result_compression_chars", DEFAULT_TOOL_RESULT_COMPRESSION_CHARS
                )
            ),
            summary_max_chars=int(payload.get("summary_max_chars", DEFAULT_SUMMARY_MAX_CHARS)),
        )
        raw_turns = payload.get("turns", [])
        if not isinstance(raw_turns, list):
            raise ValueError("Invalid serialized context turns")
        context._turns = [ContextTurn.from_dict(item) for item in raw_turns if isinstance(item, dict)]
        summary = payload.get("summary")
        context.summary = summary if isinstance(summary, str) and summary.strip() else None
        context.compressed_tool_results = int(payload.get("compressed_tool_results", 0))
        context.compaction_count = int(payload.get("compaction_count", 0))
        context.summarized_turns = int(
            payload.get("summarized_turns", payload.get("pruned_turns", 0))
        )
        context.summary_failures = int(payload.get("summary_failures", 0))
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

    @property
    def approximate_size_chars(self) -> int:
        return len(json.dumps(self._system, ensure_ascii=False)) + (
            len(self.summary) if self.summary else 0
        ) + sum(turn.approximate_size_chars for turn in self._turns)

    def _active_turn(self) -> ContextTurn:
        if not self._turns or self._turns[-1].complete:
            raise RuntimeError("No active user turn")
        return self._turns[-1]

    def _summary_candidate_indexes(self) -> list[int]:
        complete = [index for index, turn in enumerate(self._turns) if turn.complete]
        if len(complete) <= self.recent_completed_turns:
            return []
        return complete[: -self.recent_completed_turns]

    def _compress_old_tool_results(self) -> None:
        for turn_index in self._summary_candidate_indexes():
            turn = self._turns[turn_index]
            for block_index, block in enumerate(turn.blocks):
                replacement, count = self._compressed_block(block)
                if count:
                    turn.blocks[block_index] = replacement
                    self.compressed_tool_results += count

    def _compressed_block(self, block: ContextBlock) -> tuple[ContextBlock, int]:
        if len(block.messages) < 2 or block.messages[0].get("role") != "assistant":
            return block, 0
        calls = block.messages[0].get("tool_calls")
        if not isinstance(calls, list):
            return block, 0
        names: dict[str, str] = {}
        for call in calls:
            if not isinstance(call, dict):
                continue
            function = call.get("function")
            if isinstance(call.get("id"), str) and isinstance(function, dict):
                name = function.get("name")
                if isinstance(name, str):
                    names[call["id"]] = name

        changed = 0
        messages = [deepcopy(message) for message in block.messages]
        for message in messages[1:]:
            call_id = message.get("tool_call_id")
            content = message.get("content")
            tool_name = names.get(call_id) if isinstance(call_id, str) else None
            if (
                tool_name not in COMPRESSIBLE_TOOL_NAMES
                or not isinstance(content, str)
                or len(content) <= self.tool_result_compression_chars
                or _is_compacted_tool_result(content)
            ):
                continue
            message["content"] = _tool_result_placeholder(tool_name, call_id, content)
            changed += 1
        return (ContextBlock(tuple(messages)), changed) if changed else (block, 0)


def _tool_result_placeholder(tool_name: str, call_id: str, content: str) -> str:
    ok: bool | None = None
    try:
        payload = json.loads(content)
        if isinstance(payload, dict) and isinstance(payload.get("ok"), bool):
            ok = payload["ok"]
    except json.JSONDecodeError:
        pass
    return json.dumps(
        {
            "ok": ok,
            "compacted": True,
            "tool": tool_name,
            "tool_call_id": call_id,
            "original_chars": len(content),
            "message": (
                "Older read-only tool output was omitted from working context. "
                "Re-run the tool if current details are needed."
            ),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _is_compacted_tool_result(content: str) -> bool:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return False
    return isinstance(payload, dict) and payload.get("compacted") is True


def _fingerprint(summary: str | None, candidates: list[JsonObject]) -> str:
    rendered = json.dumps(
        {"summary": summary, "turns": candidates},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()
