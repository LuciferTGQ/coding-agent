from __future__ import annotations

import os
import json
from pathlib import Path
from threading import Event, Lock
import time
from typing import Sequence

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog

from coding_agent.agent import AgentEvent, AgentResult
from coding_agent.llm import AssistantResponse, ToolCall
import coding_agent.gui.app as gui_app
from coding_agent.gui.app import MainWindow
from coding_agent.gui.settings import AppSettings, SettingsStore
from coding_agent.gui.sidebar import SessionRow
from coding_agent.gui.widgets import MessageBubble
from coding_agent.sessions import SessionStore
from coding_agent.session_runtime import SessionRuntime as RealSessionRuntime


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

    window._handle_session_event(session.id, AgentEvent("reasoning_delta", 1, "checking"))
    window._handle_session_event(
        session.id,
        AgentEvent("tool_call", 1, '{"path":"x.py"}', "read_file", None, "c1")
    )
    window._handle_session_event(
        session.id,
        AgentEvent("tool_result", 1, "contents", "read_file", True, "c1"),
    )
    window._handle_session_event(session.id, AgentEvent("content_delta", 2, "Done"))
    window._handle_session_event(session.id, AgentEvent("final", 2, "Done"))

    assert "c1" in window.conversation._tools
    assert "已完成" in window.conversation._tools["c1"].toggle.text()
    window.close()
    app.processEvents()


def test_tool_cards_update_by_call_id_when_results_arrive_out_of_order(
    tmp_path: Path,
) -> None:
    app = _app()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "state")
    session = store.create(workspace=workspace)
    window = MainWindow(store)
    window.load_session(session.id)

    for call_id in ("slow", "fast"):
        window._handle_session_event(
            session.id,
            AgentEvent("tool_call", 1, call_id, "delegate_task", None, call_id),
        )
    window._handle_session_event(
        session.id,
        AgentEvent("tool_result", 1, "fast findings", "delegate_task", True, "fast"),
    )

    assert "执行中" in window.conversation._tools["slow"].toggle.text()
    assert "已完成" in window.conversation._tools["fast"].toggle.text()
    assert "fast findings" in window.conversation._tools["fast"].detail.toPlainText()

    window._handle_session_event(
        session.id,
        AgentEvent("tool_result", 1, "slow findings", "delegate_task", True, "slow"),
    )
    assert "已完成" in window.conversation._tools["slow"].toggle.text()
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
    assert window.subagent_label.text() == "Sub-agents"
    assert window.language == "en"
    assert window.settings.max_steps == 17
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


def test_background_events_do_not_render_in_current_conversation(tmp_path: Path) -> None:
    app = _app()
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    store = SessionStore(tmp_path / "routing-state")
    session_a = store.create(workspace=workspace_a, title="A")
    session_b = store.create(workspace=workspace_b, title="B")
    window = MainWindow(store)
    window.load_session(session_b.id)

    window._handle_session_event(
        session_a.id,
        AgentEvent("tool_call", 1, "background", "read_file", None, "a-call"),
    )
    assert "a-call" not in window.conversation._tools

    window._handle_session_event(
        session_b.id,
        AgentEvent("tool_call", 1, "foreground", "read_file", None, "b-call"),
    )
    assert "b-call" in window.conversation._tools
    window.close()
    app.processEvents()


def test_switching_sessions_hides_detached_transcript_widgets(tmp_path: Path) -> None:
    app = _app()
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    store = SessionStore(tmp_path / "switch-visual-state")
    session_a = store.create(workspace=workspace_a, title="A")
    session_b = store.create(workspace=workspace_b, title="B")
    session_a.transcript = [{"type": "user", "text": "only A"}]
    store.save(session_a)
    window = MainWindow(store)
    window.show()
    window.load_session(session_a.id)
    old_bubble = window.conversation.layout.itemAt(0).widget()

    window.load_session(session_b.id)
    app.processEvents()

    assert old_bubble.isHidden()
    assert old_bubble.parent() is None
    window.close()
    app.processEvents()


def test_background_completion_and_failure_update_sidebar_state(tmp_path: Path) -> None:
    app = _app()
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    store = SessionStore(tmp_path / "background-state")
    session_a = store.create(workspace=workspace_a, title="A")
    session_b = store.create(workspace=workspace_b, title="B")
    window = MainWindow(store)
    window.load_session(session_b.id)
    result = AgentResult("done", 1, "completed", False, False)

    window._task_completed(session_a.id, result)
    assert store.load(session_a.id).unread is True

    window._task_failed(session_a.id, "agent", "failed")
    assert session_a.id in window.failed_session_ids
    assert store.load(session_a.id).unread is True
    window.close()
    app.processEvents()


def test_running_session_does_not_disable_navigation_or_draft_editor(tmp_path: Path) -> None:
    class RunningWorker:
        def request_stop(self) -> None:
            pass

    app = _app()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "running-state")
    session = store.create(workspace=workspace)
    window = MainWindow(store)
    window.task_manager.workers[session.id] = RunningWorker()
    window._sync_controls()

    assert window.editor.isEnabled()
    assert window.new_button.isEnabled()
    assert window.session_list.isEnabled()
    assert not window.send_button.isEnabled()
    assert not window.subagent_toggle.isEnabled()
    assert not window.stop_button.isHidden()
    window.task_manager.workers.clear()
    window.close()
    app.processEvents()


def test_subagent_toggle_defaults_off_and_persists_per_session(tmp_path: Path) -> None:
    app = _app()
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    store = SessionStore(tmp_path / "subagent-toggle-state")
    session_a = store.create(workspace=workspace_a, title="A")
    session_a.transcript = [{"type": "user", "text": "keep history"}]
    store.save(session_a)
    session_b = store.create(
        workspace=workspace_b,
        title="B",
        subagents_enabled=True,
    )
    window = MainWindow(store)

    window.load_session(session_a.id)
    assert not window.subagent_toggle.isChecked()
    window.subagent_toggle.setChecked(True)
    assert store.load(session_a.id).subagents_enabled is True
    assert store.load(session_a.id).transcript[0]["text"] == "keep history"
    assert store.load(session_a.id).workspace == str(workspace_a.resolve())

    window.load_session(session_b.id)
    assert window.subagent_toggle.isChecked()
    window.subagent_toggle.setChecked(False)
    assert store.load(session_b.id).subagents_enabled is False

    window.load_session(session_a.id)
    assert window.subagent_toggle.isChecked()
    window.close()
    app.processEvents()


def test_same_workspace_running_session_is_detected(tmp_path: Path) -> None:
    app = _app()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "conflict-state")
    session_a = store.create(workspace=workspace, title="A")
    session_b = store.create(workspace=workspace, title="B")
    window = MainWindow(store)
    window.task_manager.workers[session_a.id] = object()

    assert window._same_workspace_running_sessions(session_b) == [session_a.id]
    window.task_manager.workers.clear()
    window.close()
    app.processEvents()


def test_same_workspace_warning_cancel_preserves_draft_and_does_not_start(
    tmp_path: Path, monkeypatch
) -> None:
    app = _app()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "conflict-cancel-state")
    session_a = store.create(workspace=workspace, title="A")
    session_b = store.create(workspace=workspace, title="B")
    window = MainWindow(store)
    window.task_manager.workers[session_a.id] = object()
    window.load_session(session_b.id)
    window.editor.setPlainText("keep this draft")
    window.attachments = ["notes.txt"]
    window._update_attachments()
    monkeypatch.setattr(window, "_confirm_same_workspace_concurrency", lambda: False)

    window.send_message()

    assert window.editor.toPlainText() == "keep this draft"
    assert window.attachments == ["notes.txt"]
    assert not window.task_manager.is_running(session_b.id)
    assert store.load(session_b.id).transcript == []
    window.task_manager.workers.clear()
    window.close()
    app.processEvents()


def test_main_window_runs_two_sessions_and_marks_background_completion_unread(
    tmp_path: Path, monkeypatch
) -> None:
    app = _app()
    runtimes = []

    class BlockingRuntime:
        def __init__(self, *args, **kwargs) -> None:
            self.language = kwargs.get("language")
            self.max_steps = kwargs.get("max_steps")
            self.started = Event()
            self.release = Event()
            self.session_id = ""
            runtimes.append(self)

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
            self.session_id = session_id
            self.started.set()
            on_event(AgentEvent("model", 1, f"running {message}"))
            while not self.release.wait(0.01):
                if should_cancel():
                    return AgentResult("stopped", 1, "cancelled", False, False)
            on_event(AgentEvent("final", 1, f"finished {message}"))
            return AgentResult("done", 1, "completed", False, False)

    monkeypatch.setattr(gui_app, "SessionRuntime", BlockingRuntime)
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    store = SessionStore(tmp_path / "window-parallel-state")
    session_a = store.create(workspace=workspace_a, title="A")
    session_b = store.create(workspace=workspace_b, title="B")
    window = MainWindow(store)

    window.load_session(session_a.id)
    window.editor.setPlainText("task A")
    window.send_message()
    window.load_session(session_b.id)
    window.editor.setPlainText("task B")
    window.send_message()
    deadline = time.monotonic() + 2
    active = [runtime for runtime in runtimes if runtime.session_id]
    while len(active) < 2 and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
        active = [runtime for runtime in runtimes if runtime.session_id]
    assert len(active) == 2
    assert all(runtime.started.wait(1) for runtime in active)
    assert all(runtime.language == "zh" for runtime in active)
    assert window.task_manager.running_count() == 2

    runtime_a = next(runtime for runtime in active if runtime.session_id == session_a.id)
    runtime_a.release.set()
    deadline = time.monotonic() + 2
    while window.task_manager.is_running(session_a.id) and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert not window.task_manager.is_running(session_a.id)
    assert window.task_manager.is_running(session_b.id)
    assert store.load(session_a.id).unread is True

    window.stop_run()
    deadline = time.monotonic() + 2
    while window.task_manager.running_count() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert window.task_manager.running_count() == 0
    window.close()
    app.processEvents()


def test_two_gui_sessions_remain_isolated_while_one_runs_parallel_children(
    tmp_path: Path, monkeypatch
) -> None:
    app = _app()
    children_started = Event()
    release_children = Event()
    session_b_started = Event()
    release_session_b = Event()
    factory_lock = Lock()
    child_count = 0
    parent_a_requests = []

    def response(content: str = "", *calls: tuple[str, str, dict]) -> AssistantResponse:
        tool_calls = tuple(
            ToolCall(call_id, name, json.dumps(arguments))
            for call_id, name, arguments in calls
        )
        provider = {"role": "assistant", "content": content}
        if tool_calls:
            provider["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments},
                }
                for call in tool_calls
            ]
        return AssistantResponse(content, tool_calls, provider)

    class ParentAModel:
        def __init__(self) -> None:
            self.step = 0

        def complete(self, *, messages: Sequence[dict], tools: Sequence[dict]) -> AssistantResponse:
            parent_a_requests.append((list(messages), list(tools)))
            self.step += 1
            if self.step == 1:
                assert "delegate_task" in [item["function"]["name"] for item in tools]
                return response(
                    "",
                    ("a-child-1", "delegate_task", {"task": "inspect module one"}),
                    ("a-child-2", "delegate_task", {"task": "inspect module two"}),
                )
            return response("A completed from child findings")

    class ChildModel:
        def complete(self, *, messages: Sequence[dict], tools: Sequence[dict]) -> AssistantResponse:
            nonlocal child_count
            assert [item["function"]["name"] for item in tools] == [
                "list_files",
                "read_file",
                "search_text",
            ]
            with factory_lock:
                child_count += 1
                if child_count == 2:
                    children_started.set()
            assert release_children.wait(3)
            return response("condensed child finding")

    class SessionBModel:
        def complete(self, *, messages: Sequence[dict], tools: Sequence[dict]) -> AssistantResponse:
            assert "delegate_task" not in [item["function"]["name"] for item in tools]
            session_b_started.set()
            assert release_session_b.wait(3)
            return response("B completed independently")

    class RoutedRuntime:
        def __init__(self, store, **kwargs) -> None:
            self.store = store
            self.kwargs = kwargs

        def run_turn(self, session_id, message, **kwargs) -> AgentResult:
            created = 0
            creation_lock = Lock()

            def factory(_config):
                nonlocal created
                if session_id == session_b.id:
                    return SessionBModel()
                with creation_lock:
                    created += 1
                    return ParentAModel() if created == 1 else ChildModel()

            return RealSessionRuntime(
                self.store,
                model_factory=factory,
                language=self.kwargs.get("language"),
                max_steps=self.kwargs.get("max_steps"),
            ).run_turn(session_id, message, **kwargs)

    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    store = SessionStore(tmp_path / "combined-parallel-state")
    session_a = store.create(
        workspace=workspace_a,
        title="A",
        subagents_enabled=True,
    )
    session_b = store.create(workspace=workspace_b, title="B")
    monkeypatch.setattr(gui_app, "SessionRuntime", RoutedRuntime)
    window = MainWindow(store)
    try:
        window.load_session(session_a.id)
        window.editor.setPlainText("task A")
        window.send_message()
        window.load_session(session_b.id)
        window.editor.setPlainText("task B")
        window.send_message()

        deadline = time.monotonic() + 3
        while (
            not (children_started.is_set() and session_b_started.is_set())
            and time.monotonic() < deadline
        ):
            app.processEvents()
        assert children_started.is_set()
        assert session_b_started.is_set()
        assert window.task_manager.running_count() == 2
        assert "a-child-1" not in window.conversation._tools

        release_children.set()
        deadline = time.monotonic() + 3
        while window.task_manager.is_running(session_a.id) and time.monotonic() < deadline:
            app.processEvents()
        assert not window.task_manager.is_running(session_a.id)
        assert window.task_manager.is_running(session_b.id)
        assert store.load(session_a.id).unread is True

        tool_results = [
            message for message in parent_a_requests[1][0] if message.get("role") == "tool"
        ]
        assert [message["tool_call_id"] for message in tool_results] == [
            "a-child-1",
            "a-child-2",
        ]
        assert len(store.list()) == 2
        assert not any(
            item.get("name") == "delegate_task"
            for item in store.load(session_b.id).transcript
        )
    finally:
        release_children.set()
        release_session_b.set()
        deadline = time.monotonic() + 3
        while window.task_manager.running_count() and time.monotonic() < deadline:
            app.processEvents()
        window.close()
        app.processEvents()


def test_close_requests_cooperative_stop_all(tmp_path: Path, monkeypatch) -> None:
    class RunningWorker:
        def __init__(self) -> None:
            self.stopped = False

        def request_stop(self) -> None:
            self.stopped = True

    app = _app()
    store = SessionStore(tmp_path / "close-state")
    window = MainWindow(store)
    window.show()
    workers = [RunningWorker(), RunningWorker()]
    window.task_manager.workers.update({"a": workers[0], "b": workers[1]})
    monkeypatch.setattr(window, "_confirm_stop_all", lambda count: count == 2)

    window.close()
    app.processEvents()

    assert window.isVisible()
    assert window._closing_after_tasks is True
    assert all(worker.stopped for worker in workers)
    window.task_manager.workers.clear()
    window.close()
    app.processEvents()
    assert not window.isVisible()


def test_language_restart_is_deferred_while_tasks_run(
    tmp_path: Path, monkeypatch
) -> None:
    app = _app()
    store = SessionStore(tmp_path / "deferred-restart-state")
    window = MainWindow(store)
    window.task_manager.workers["running"] = object()
    notices = []
    monkeypatch.setattr(
        gui_app.QMessageBox,
        "information",
        lambda *args: notices.append(args) or gui_app.QMessageBox.Ok,
    )
    monkeypatch.setattr(
        window,
        "_confirm_restart",
        lambda: (_ for _ in ()).throw(AssertionError("restart prompt must be deferred")),
    )

    window._apply_settings(AppSettings(language="en"))

    assert SettingsStore(store.root).load().language == "en"
    assert notices
    window.task_manager.workers.clear()
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
