from __future__ import annotations

from threading import Event
import time

from PySide6.QtCore import QCoreApplication, QObject, Signal

from coding_agent.agent import AgentEvent, AgentResult
from coding_agent.gui.task_manager import AgentTaskManager


class FakeWorker(QObject):
    event_received = Signal(object)
    completed = Signal(object)
    failed = Signal(str, str)
    finished = Signal()

    def __init__(self, **arguments) -> None:
        super().__init__()
        self.arguments = arguments
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def request_stop(self) -> None:
        self.stopped = True


def test_independent_sessions_run_and_route_events() -> None:
    workers: list[FakeWorker] = []

    def factory(**arguments) -> FakeWorker:
        worker = FakeWorker(**arguments)
        workers.append(worker)
        return worker

    manager = AgentTaskManager(worker_factory=factory)
    events = []
    manager.event_received.connect(lambda session_id, event: events.append((session_id, event)))

    manager.start(runtime=object(), session_id="a", message="A", attachments=[])
    manager.start(runtime=object(), session_id="b", message="B", attachments=[])
    workers[0].event_received.emit(AgentEvent("model", 1, "from A"))
    workers[1].event_received.emit(AgentEvent("model", 1, "from B"))

    assert manager.running_count() == 2
    assert manager.running_session_ids() == ("a", "b")
    assert [(session_id, event.message) for session_id, event in events] == [
        ("a", "from A"),
        ("b", "from B"),
    ]


def test_same_session_is_rejected_and_stop_is_scoped() -> None:
    workers: list[FakeWorker] = []

    def factory(**arguments) -> FakeWorker:
        worker = FakeWorker(**arguments)
        workers.append(worker)
        return worker

    manager = AgentTaskManager(worker_factory=factory)
    manager.start(runtime=object(), session_id="a", message="A", attachments=[])
    manager.start(runtime=object(), session_id="b", message="B", attachments=[])

    try:
        manager.start(runtime=object(), session_id="a", message="again", attachments=[])
    except ValueError as exc:
        assert "already" in str(exc)
    else:
        raise AssertionError("expected duplicate session rejection")

    assert manager.stop("a") is True
    assert workers[0].stopped is True
    assert workers[1].stopped is False
    assert manager.stop("missing") is False


def test_finished_workers_are_removed_and_stop_all_is_cooperative() -> None:
    workers: list[FakeWorker] = []

    def factory(**arguments) -> FakeWorker:
        worker = FakeWorker(**arguments)
        workers.append(worker)
        return worker

    manager = AgentTaskManager(worker_factory=factory)
    finished = []
    manager.finished.connect(finished.append)
    manager.start(runtime=object(), session_id="a", message="A", attachments=[])
    manager.start(runtime=object(), session_id="b", message="B", attachments=[])

    manager.stop_all()
    assert all(worker.stopped for worker in workers)
    workers[0].completed.emit(
        AgentResult("done", 1, "completed", False, False)
    )
    workers[0].finished.emit()

    assert not manager.is_running("a")
    assert manager.is_running("b")
    assert finished == ["a"]


def test_real_workers_run_concurrently_and_stop_independently() -> None:
    app = QCoreApplication.instance() or QCoreApplication([])

    class BlockingRuntime:
        def __init__(self) -> None:
            self.started = Event()
            self.release = Event()

        def run_turn(
            self,
            session_id,
            message,
            *,
            attachments,
            on_event,
            should_cancel,
            stream,
        ) -> AgentResult:
            self.started.set()
            on_event(AgentEvent("model", 1, f"started {session_id}"))
            while not self.release.wait(0.01):
                if should_cancel():
                    return AgentResult("stopped", 1, "cancelled", False, False)
            return AgentResult("done", 1, "completed", False, False)

    runtime_a = BlockingRuntime()
    runtime_b = BlockingRuntime()
    manager = AgentTaskManager()
    manager.start(runtime=runtime_a, session_id="a", message="A", attachments=[])
    manager.start(runtime=runtime_b, session_id="b", message="B", attachments=[])

    assert runtime_a.started.wait(1)
    assert runtime_b.started.wait(1)
    assert manager.running_count() == 2

    runtime_a.release.set()
    deadline = time.monotonic() + 2
    while manager.is_running("a") and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert not manager.is_running("a")
    assert manager.is_running("b")

    manager.stop("b")
    deadline = time.monotonic() + 2
    while manager.is_running("b") and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert not manager.is_running("b")
