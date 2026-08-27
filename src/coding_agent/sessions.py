"""Persistent desktop conversation sessions stored outside project workspaces."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4


JsonObject = dict[str, Any]
SCHEMA_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class Session:
    id: str
    title: str
    workspace: str
    model: str = "deepseek-v4-flash"
    reasoning_effort: str = "high"
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    transcript: list[JsonObject] = field(default_factory=list)
    model_context: JsonObject | None = None

    def to_dict(self) -> JsonObject:
        return {"schema_version": SCHEMA_VERSION, **asdict(self)}

    @classmethod
    def from_dict(cls, payload: JsonObject) -> "Session":
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("Unsupported session schema version")
        required = ("id", "title", "workspace", "model", "reasoning_effort")
        if not all(isinstance(payload.get(name), str) for name in required):
            raise ValueError("Session metadata is malformed")
        transcript = payload.get("transcript", [])
        context = payload.get("model_context")
        if not isinstance(transcript, list) or (context is not None and not isinstance(context, dict)):
            raise ValueError("Session conversation data is malformed")
        return cls(
            id=payload["id"],
            title=payload["title"],
            workspace=payload["workspace"],
            model=payload["model"],
            reasoning_effort=payload["reasoning_effort"],
            created_at=str(payload.get("created_at", _now())),
            updated_at=str(payload.get("updated_at", _now())),
            transcript=transcript,
            model_context=context,
        )


class SessionStore:
    """Atomic JSON CRUD for local desktop sessions; credentials are never accepted."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root or Path.home() / ".nju-coding-agent").expanduser().resolve()
        self.sessions_dir = self.root / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def create(
        self,
        *,
        workspace: str | Path,
        title: str = "New conversation",
        model: str = "deepseek-v4-flash",
        reasoning_effort: str = "high",
    ) -> Session:
        resolved = Path(workspace).expanduser().resolve()
        if not resolved.is_dir():
            raise ValueError(f"Workspace is not a directory: {resolved}")
        session = Session(
            id=uuid4().hex,
            title=title.strip() or "New conversation",
            workspace=str(resolved),
            model=model,
            reasoning_effort=reasoning_effort,
        )
        self.save(session)
        return session

    def save(self, session: Session) -> None:
        if any("api_key" in key.casefold() for key in session.to_dict()):
            raise ValueError("Credentials must not be stored in sessions")
        session.updated_at = _now()
        target = self._path(session.id)
        temporary = target.with_suffix(".json.tmp")
        payload = json.dumps(session.to_dict(), ensure_ascii=False, indent=2)
        temporary.write_text(payload + "\n", encoding="utf-8")
        os.replace(temporary, target)

    def load(self, session_id: str) -> Session:
        try:
            payload = json.loads(self._path(session_id).read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise KeyError(f"Unknown session: {session_id}") from exc
        if not isinstance(payload, dict):
            raise ValueError("Session file must contain a JSON object")
        return Session.from_dict(payload)

    def list(self) -> list[Session]:
        sessions: list[Session] = []
        for path in self.sessions_dir.glob("*.json"):
            try:
                sessions.append(Session.from_dict(json.loads(path.read_text(encoding="utf-8"))))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
        return sorted(sessions, key=lambda item: item.updated_at, reverse=True)

    def delete(self, session_id: str) -> None:
        try:
            self._path(session_id).unlink()
        except FileNotFoundError as exc:
            raise KeyError(f"Unknown session: {session_id}") from exc

    def _path(self, session_id: str) -> Path:
        if not session_id or any(character not in "0123456789abcdef" for character in session_id):
            raise ValueError("Invalid session id")
        return self.sessions_dir / f"{session_id}.json"


def title_from_message(message: str, limit: int = 48) -> str:
    single_line = " ".join(message.split())
    return single_line if len(single_line) <= limit else single_line[: limit - 1] + "…"
