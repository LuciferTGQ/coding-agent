"""Qt worker thread for blocking model and local tool execution."""

from __future__ import annotations

from threading import Event

from PySide6.QtCore import QThread, Signal

from coding_agent.agent import AgentEvent, AgentResult
from coding_agent.session_runtime import SessionRuntime
from coding_agent.sessions import SessionPersistenceError


class AgentWorker(QThread):
    event_received = Signal(object)
    completed = Signal(object)
    failed = Signal(str, str)

    def __init__(
        self,
        *,
        runtime: SessionRuntime,
        session_id: str,
        message: str,
        attachments: list[str],
    ) -> None:
        super().__init__()
        self.runtime = runtime
        self.session_id = session_id
        self.message = message
        self.attachments = attachments
        self._cancel = Event()

    def request_stop(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            result: AgentResult = self.runtime.run_turn(
                self.session_id,
                self.message,
                attachments=self.attachments,
                on_event=self.event_received.emit,
                should_cancel=self._cancel.is_set,
                stream=True,
            )
        except SessionPersistenceError as exc:
            self.failed.emit("persistence", f"{type(exc).__name__}: {exc}")
        except Exception as exc:
            self.failed.emit("agent", f"{type(exc).__name__}: {exc}")
        else:
            self.completed.emit(result)
