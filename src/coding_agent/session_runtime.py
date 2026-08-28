"""Composition layer for one persistent desktop session turn."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Callable, Iterable

from coding_agent.agent import AgentEvent, AgentResult, AgentRunner
from coding_agent.config import Config
from coding_agent.context import ContextManager
from coding_agent.llm import DeepSeekChatClient, ModelClient
from coding_agent.prompts import build_system_prompt
from coding_agent.sessions import (
    Session,
    SessionPersistenceError,
    SessionStore,
    title_from_message,
)
from coding_agent.tools import ToolRegistry, create_command_tool, create_file_tools
from coding_agent.workspace import Workspace


EventCallback = Callable[[AgentEvent], None]
ModelFactory = Callable[[Config], ModelClient]
LOGGER = logging.getLogger(__name__)


class SessionRuntime:
    """Run agent turns and persist both UI transcript and resumable model context."""

    def __init__(
        self,
        store: SessionStore,
        model_factory: ModelFactory | None = None,
        *,
        language: str | None = None,
        max_steps: int | None = None,
    ) -> None:
        self.store = store
        self._requires_api_key = model_factory is None
        self.model_factory = model_factory or self._default_model
        self.language = language if language in {"zh", "en"} else None
        self.max_steps = max_steps

    def run_turn(
        self,
        session_id: str,
        message: str,
        *,
        attachments: Iterable[str] = (),
        on_event: EventCallback | None = None,
        should_cancel: Callable[[], bool] | None = None,
        stream: bool = True,
    ) -> AgentResult:
        session = self.store.load(session_id)
        external_callback = on_event or (lambda _: None)
        persistence_warning_active = False
        pending_metadata_changes: dict[str, object] = {}

        def report_persistence_failure(exc: SessionPersistenceError) -> None:
            nonlocal persistence_warning_active
            LOGGER.warning("Session persistence failed for %s: %s", session_id, exc)
            if persistence_warning_active:
                return
            persistence_warning_active = True
            warning = AgentEvent(
                kind="persistence_warning",
                step=0,
                message=str(exc),
                ok=False,
            )
            self._record_event(session, warning)
            external_callback(warning)

        def persist_session() -> bool:
            nonlocal persistence_warning_active, session
            if pending_metadata_changes:
                try:
                    updated = self.store.update_metadata(
                        session.id, **pending_metadata_changes
                    )
                except SessionPersistenceError as exc:
                    report_persistence_failure(exc)
                    return False
                updated.transcript = session.transcript
                updated.model_context = session.model_context
                session = updated
                pending_metadata_changes.clear()
            try:
                self.store.save_runtime(session)
            except SessionPersistenceError as exc:
                report_persistence_failure(exc)
                return False
            if persistence_warning_active:
                persistence_warning_active = False
                external_callback(
                    AgentEvent(
                        kind="persistence_recovered",
                        step=0,
                        message="Session persistence recovered.",
                        ok=True,
                    )
                )
            return True

        response_language = self.language or session.preferred_language
        session.preferred_language = response_language
        workspace = Workspace(Path(session.workspace))
        attached = self._validate_attachments(workspace, attachments)
        model_message = message.strip()
        if attached:
            model_message += "\n\nAttached workspace files (read them with local tools):\n" + "\n".join(
                f"- {path}" for path in attached
            )
        if not model_message:
            raise ValueError("Message must not be empty")

        metadata_changes = {"preferred_language": response_language}
        if not session.transcript:
            metadata_changes["title"] = title_from_message(message)
        try:
            session = self.store.update_metadata(session.id, **metadata_changes)
        except SessionPersistenceError as exc:
            session.preferred_language = response_language
            if not session.transcript:
                session.title = title_from_message(message)
            pending_metadata_changes.update(metadata_changes)
            report_persistence_failure(exc)
        session.transcript.append(
            {
                "type": "user",
                "text": message.strip(),
                "attachments": attached,
                "timestamp": _timestamp(),
            }
        )
        persist_session()

        config = Config.from_sources(
            workspace=session.workspace,
            model=session.model,
            reasoning_effort=session.reasoning_effort,
            max_steps=self.max_steps,
            require_api_key=self._requires_api_key,
        )
        context = (
            ContextManager.from_dict(session.model_context)
            if session.model_context
            else ContextManager(
                system_prompt=build_system_prompt(
                    workspace.root, response_language=response_language
                ),
                soft_budget=config.context_soft_budget,
            )
        )
        context.set_system_prompt(
            build_system_prompt(workspace.root, response_language=response_language)
        )
        registry = ToolRegistry(
            [
                *create_file_tools(
                    workspace,
                    max_read_lines=config.max_read_lines,
                    max_write_chars=config.max_write_chars,
                    max_search_results=config.max_search_results,
                ),
                create_command_tool(
                    workspace,
                    default_timeout=config.command_timeout,
                    output_limit=config.tool_output_limit,
                ),
            ],
            output_limit=config.tool_output_limit,
        )
        def record(event: AgentEvent) -> None:
            self._record_event(session, event)
            external_callback(event)
            if event.kind not in {"reasoning_delta", "content_delta", "step"}:
                persist_session()

        runner = AgentRunner(
            model=self.model_factory(config),
            tools=registry,
            context=context,
            max_steps=config.max_steps,
            on_event=record,
            stream=stream,
            should_cancel=should_cancel,
        )
        try:
            result = runner.run(model_message)
        except Exception as exc:
            session.transcript.append(
                {
                    "type": "status",
                    "kind": "error",
                    "text": f"{type(exc).__name__}: {exc}",
                    "ok": False,
                    "timestamp": _timestamp(),
                }
            )
            session.model_context = context.to_dict()
            persist_session()
            raise
        session.model_context = context.to_dict()
        persist_session()
        return result

    @staticmethod
    def _record_event(session: Session, event: AgentEvent) -> None:
        if event.kind in {"reasoning_delta", "content_delta"}:
            item_type = "reasoning" if event.kind == "reasoning_delta" else "assistant_stream"
            if session.transcript and session.transcript[-1].get("type") == item_type:
                session.transcript[-1]["text"] += event.message
            else:
                session.transcript.append(
                    {"type": item_type, "text": event.message, "timestamp": _timestamp()}
                )
            return
        if event.kind == "model":
            session.transcript.append(
                {"type": "assistant_stream", "text": event.message, "timestamp": _timestamp()}
            )
        elif event.kind == "tool_call":
            session.transcript.append(
                {
                    "type": "tool_call",
                    "name": event.tool_name,
                    "call_id": event.call_id,
                    "text": event.message,
                    "status": "running",
                    "timestamp": _timestamp(),
                }
            )
        elif event.kind == "tool_result":
            session.transcript.append(
                {
                    "type": "tool_result",
                    "name": event.tool_name,
                    "call_id": event.call_id,
                    "text": event.message,
                    "ok": event.ok,
                    "timestamp": _timestamp(),
                }
            )
        elif event.kind == "final":
            if session.transcript and session.transcript[-1].get("type") == "assistant_stream":
                session.transcript[-1]["type"] = "assistant"
                session.transcript[-1]["text"] = event.message
            else:
                session.transcript.append(
                    {"type": "assistant", "text": event.message, "timestamp": _timestamp()}
                )
        elif event.kind in {
            "verification",
            "stopped",
            "persistence_warning",
            "persistence_recovered",
        }:
            session.transcript.append(
                {
                    "type": "status",
                    "kind": event.kind,
                    "text": event.message,
                    "ok": event.ok,
                    "timestamp": _timestamp(),
                }
            )

    @staticmethod
    def _validate_attachments(workspace: Workspace, attachments: Iterable[str]) -> list[str]:
        validated: list[str] = []
        for attachment in attachments:
            target = workspace.resolve_path(attachment, must_exist=True, allow_root=False)
            if not target.is_file():
                raise ValueError(f"Attachment is not a file: {attachment}")
            target.read_text(encoding="utf-8")
            validated.append(target.relative_to(workspace.root).as_posix())
        return validated

    @staticmethod
    def _default_model(config: Config) -> DeepSeekChatClient:
        return DeepSeekChatClient(
            api_key=config.api_key,
            base_url=config.base_url,
            model=config.model,
            reasoning_effort=config.reasoning_effort,
            thinking_enabled=config.thinking_enabled,
        )


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()
