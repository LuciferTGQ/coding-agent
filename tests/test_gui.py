from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog

from coding_agent.agent import AgentEvent
import coding_agent.gui.app as gui_app
from coding_agent.gui.app import MainWindow
from coding_agent.gui.settings import AppSettings, SettingsStore
from coding_agent.gui.sidebar import SessionRow
from coding_agent.gui.widgets import MessageBubble
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
    assert window.new_button.text() == "＋  新建对话"
    assert window.settings_button.text() == "设置"
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
    assert "已完成" in window.conversation._tools["c1"].toggle.text()
    window.close()
    app.processEvents()


def test_empty_window_keeps_new_conversation_available(tmp_path: Path) -> None:
    app = _app()
    window = MainWindow(SessionStore(tmp_path / "empty-state"))
    assert window.new_button.isEnabled()
    assert not window.send_button.isEnabled()
    status = window.conversation.layout.itemAt(0).widget()
    assert status.text() == "新建对话并选择一个项目工作区。"
    assert status.height() >= status.minimumHeight()
    window.close()
    app.processEvents()


def test_saved_english_setting_applies_to_next_window(tmp_path: Path) -> None:
    app = _app()
    store = SessionStore(tmp_path / "english-state")
    SettingsStore(store.root).save(
        AppSettings(language="en", reasoning_effort="low", max_steps=17)
    )

    window = MainWindow(store)

    assert window.new_button.text() == "＋  New conversation"
    assert window.settings_button.text() == "Settings"
    assert window.runtime.language == "en"
    assert window.runtime.max_steps == 17
    window.close()
    app.processEvents()


def test_search_filters_title_and_workspace_without_loading_transcript(tmp_path: Path) -> None:
    app = _app()
    alpha_workspace = tmp_path / "alpha-project"
    beta_workspace = tmp_path / "beta-project"
    alpha_workspace.mkdir()
    beta_workspace.mkdir()
    store = SessionStore(tmp_path / "search-state")
    alpha = store.create(workspace=alpha_workspace, title="Fix parser")
    store.create(workspace=beta_workspace, title="Update docs")
    window = MainWindow(store)

    window.search_input.setText("parser")
    app.processEvents()
    assert window.session_list.count() == 1
    assert window.session_list.item(0).data(Qt.UserRole) == alpha.id

    window.search_input.setText("beta-project")
    app.processEvents()
    assert window.session_list.count() == 1
    window.search_input.clear()
    assert window.session_list.count() == 2
    window.close()
    app.processEvents()


def test_rename_and_unread_metadata_preserve_session_history(tmp_path: Path) -> None:
    app = _app()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "metadata-state")
    session = store.create(workspace=workspace, title="Original")
    session.transcript = [{"type": "user", "text": "keep me"}]
    session.model_context = {"system": {"role": "system", "content": "keep"}, "turns": []}
    store.save(session)
    window = MainWindow(store)

    window._rename_session(session.id, "  Renamed  ")
    window.set_session_unread(session.id, True)
    loaded = store.load(session.id)

    assert loaded.title == "Renamed"
    assert loaded.unread is True
    assert loaded.workspace == str(workspace.resolve())
    assert loaded.transcript == session.transcript
    assert loaded.model_context == session.model_context
    window.close()
    app.processEvents()


def test_opening_an_unread_session_marks_it_read(tmp_path: Path) -> None:
    app = _app()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "read-state")
    session = store.create(workspace=workspace)
    session.unread = True
    store.save(session)

    window = MainWindow(store)

    assert store.load(session.id).unread is False
    window.close()
    app.processEvents()


def test_session_row_hover_and_right_click_share_menu_entry_point(
    tmp_path: Path, monkeypatch
) -> None:
    app = _app()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "hover-state")
    store.create(workspace=workspace, title="Hover me")
    store.create(workspace=workspace, title="Selected first")
    window = MainWindow(store)
    window.show()
    app.processEvents()
    item = window.session_list.item(1)
    row = window.session_list.itemWidget(item)
    assert isinstance(row, SessionRow)
    assert row.menu_button.text() == ""
    calls = []
    monkeypatch.setattr(
        window,
        "_show_session_menu",
        lambda session_id, position: calls.append((session_id, position)),
    )

    QTest.mouseMove(row, row.rect().center())
    app.processEvents()
    assert row._hovered is True
    assert row.menu_button.isVisible()
    assert row.menu_button.text() == "..."
    assert row.property("hovered") is True

    QTest.mouseClick(row, Qt.RightButton, pos=row.rect().center())
    app.processEvents()
    assert calls and calls[0][0] == item.data(Qt.UserRole)
    window.close()
    app.processEvents()


def test_workspace_authorization_can_cancel_new_conversation(
    tmp_path: Path, monkeypatch
) -> None:
    app = _app()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "authorization-state")
    window = MainWindow(store)
    monkeypatch.setattr(
        QFileDialog,
        "getExistingDirectory",
        lambda *args, **kwargs: str(workspace),
    )
    monkeypatch.setattr(window, "_confirm_workspace_access", lambda _: False)

    window.new_session()

    assert store.list() == []
    window.close()
    app.processEvents()


def test_long_user_and_markdown_messages_expand_without_inner_scroll(tmp_path: Path) -> None:
    app = _app()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "long-message-state")
    session = store.create(workspace=workspace)
    session.transcript = [
        {"type": "user", "text": "这是一段需要完整显示的中文消息。" * 80},
        {
            "type": "assistant",
            "text": "## 完整结果\n\n" + "- 这是一条较长的 Markdown 说明。\n" * 80,
        },
    ]
    store.save(session)
    window = MainWindow(store)
    window.resize(920, 700)
    window.show()
    window.load_session(session.id)
    for _ in range(6):
        app.processEvents()

    bubbles = [
        window.conversation.layout.itemAt(index).widget()
        for index in range(window.conversation.layout.count())
        if isinstance(window.conversation.layout.itemAt(index).widget(), MessageBubble)
    ]
    assert len(bubbles) == 2
    for bubble in bubbles:
        expected = bubble.body.heightForWidth(bubble.body.width())
        assert bubble.body.height() >= expected
        assert bubble.body.geometry().bottom() <= bubble.contentsRect().bottom()
    window.close()
    app.processEvents()


def test_language_setting_can_restart_now_or_stay_until_later(
    tmp_path: Path, monkeypatch
) -> None:
    app = _app()
    store = SessionStore(tmp_path / "restart-state")
    window = MainWindow(store)
    window.show()
    restarted: list[bool] = []
    monkeypatch.setattr(window, "_confirm_restart", lambda: False)
    monkeypatch.setattr(gui_app, "start_replacement_gui", lambda: restarted.append(True) or True)

    english = AppSettings(language="en", max_steps=31)
    window._apply_settings(english)

    assert window.isVisible()
    assert restarted == []
    assert SettingsStore(store.root).load() == english
    assert window.language == "zh"

    monkeypatch.setattr(window, "_confirm_restart", lambda: True)
    window._apply_settings(english)
    app.processEvents()
    assert restarted == [True]
    assert not window.isVisible()


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
