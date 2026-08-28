"""Lifecycle manager for independent per-session agent workers."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Signal

from coding_agent.gui.worker import AgentWorker
from coding_agent.session_runtime import SessionRuntime


WorkerFactory = Callable[..., AgentWorker]


class AgentTaskManager(QObject):
    """Route worker signals by session without participating in agent reasoning."""

    event_received = Signal(str, object)
    completed = Signal(str, object)
    failed = Signal(str, str, str)
    finished = Signal(str)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        worker_factory: WorkerFactory = AgentWorker,
    ) -> None:
        super().__init__(parent)
        self.workers: dict[str, AgentWorker] = {}
        self._worker_factory = worker_factory

    def start(
        self,
        *,
        runtime: SessionRuntime,
        session_id: str,
        message: str,
        attachments: list[str],
    ) -> AgentWorker:
        if session_id in self.workers:
            raise ValueError(f"Session already has a running task: {session_id}")
        worker = self._worker_factory(
            runtime=runtime,
            session_id=session_id,
            message=message,
            attachments=attachments,
        )
        self.workers[session_id] = worker
        worker.event_received.connect(
            lambda event, target=session_id: self.event_received.emit(target, event)
        )
        worker.completed.connect(
            lambda result, target=session_id: self.completed.emit(target, result)
        )
        worker.failed.connect(
            lambda category, message, target=session_id: self.failed.emit(
                target, category, message
            )
        )
        worker.finished.connect(lambda target=session_id: self._worker_finished(target))
        worker.start()
        return worker

    def stop(self, session_id: str) -> bool:
        worker = self.workers.get(session_id)
        if worker is None:
            return False
        worker.request_stop()
        return True

    def stop_all(self) -> None:
        for worker in tuple(self.workers.values()):
            worker.request_stop()

    def is_running(self, session_id: str) -> bool:
        return session_id in self.workers

    def running_session_ids(self) -> tuple[str, ...]:
        return tuple(self.workers)

    def running_count(self) -> int:
        return len(self.workers)

    def _worker_finished(self, session_id: str) -> None:
        worker = self.workers.pop(session_id, None)
        if worker is not None:
            worker.deleteLater()
        self.finished.emit(session_id)
