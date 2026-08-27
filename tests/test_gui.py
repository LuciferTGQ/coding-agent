from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from coding_agent.agent import AgentEvent
from coding_agent.gui.app import MainWindow
from coding_agent.sessions import SessionStore


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_window_loads_persistent_session_and_renders_event_cards(tmp_path: Path) -> None:
    app = _app()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "state")
    session = store.create(workspace=workspace, title="GUI session")
    window = MainWindow(store)
    window.load_session(session.id)

    assert window.title_label.text() == "GUI session"
    assert window.workspace_label.text() == str(workspace.resolve())
    assert window.model_combo.currentText() == "deepseek-v4-flash"
    assert window.model_combo.count() == 1
    assert [window.effort_combo.itemText(i) for i in range(window.effort_combo.count())] == [
        "low",
        "high",
        "max",
    ]

    window._handle_event(AgentEvent("reasoning_delta", 1, "checking"))
    window._handle_event(
        AgentEvent("tool_call", 1, '{"path":"x.py"}', "read_file", None, "c1")
    )
    window._handle_event(AgentEvent("tool_result", 1, "contents", "read_file", True, "c1"))
    window._handle_event(AgentEvent("content_delta", 2, "Done"))
    window._handle_event(AgentEvent("final", 2, "Done"))

    assert "c1" in window.conversation._tools
    assert "completed" in window.conversation._tools["c1"].toggle.text()
    window.close()
    app.processEvents()


def test_empty_window_keeps_new_conversation_available(tmp_path: Path) -> None:
    app = _app()
    window = MainWindow(SessionStore(tmp_path / "empty-state"))
    assert window.new_button.isEnabled()
    assert not window.send_button.isEnabled()
    window.close()
    app.processEvents()


def test_long_restored_transcript_does_not_overlap_and_scrolls_to_latest(tmp_path: Path) -> None:
    app = _app()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "long-state")
    session = store.create(workspace=workspace)
    session.transcript = [
        {"type": "assistant", "text": "## Result\n\n" + "long response " * 100},
        {"type": "tool_call", "name": "run_command", "call_id": "c1", "text": "pytest"},
        {"type": "tool_result", "name": "run_command", "call_id": "c1", "text": "10 passed", "ok": True},
        {"type": "assistant", "text": "## Summary\n\n" + "restored context " * 100},
    ]
    store.save(session)
    window = MainWindow(store)
    window.show()
    window.load_session(session.id)
    for _ in range(5):
        app.processEvents()

    widgets = [
        window.conversation.layout.itemAt(index).widget()
        for index in range(window.conversation.layout.count())
    ]
    for earlier, later in zip(widgets, widgets[1:]):
        assert earlier.geometry().bottom() < later.geometry().top()
    last = widgets[-1]
    visible_bottom = window.conversation.verticalScrollBar().value() + window.conversation.viewport().height()
    assert last.geometry().top() < visible_bottom
    window.close()
    app.processEvents()
