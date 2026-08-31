"""Persistent PySide6 desktop application."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys

from PySide6.QtCore import QProcess, QTimer, Qt, QUrl
from PySide6.QtGui import QCloseEvent, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from coding_agent.agent import AgentEvent, AgentResult
from coding_agent.gui.i18n import tr
from coding_agent.gui.sidebar import SessionRow
from coding_agent.gui.settings import (
    MODELS,
    REASONING_EFFORTS,
    AppSettings,
    SettingsStore,
)
from coding_agent.gui.widgets import ConversationView
from coding_agent.gui.task_manager import AgentTaskManager
from coding_agent.gui.toggle import ToggleSwitch
from coding_agent.session_runtime import SessionRuntime
from coding_agent.sessions import Session, SessionPersistenceError, SessionStore


def start_replacement_gui() -> bool:
    """Start a fresh GUI process without terminating the current process forcibly."""

    launcher = Path(sys.argv[0]).resolve()
    if not getattr(sys, "frozen", False) and launcher.suffix.casefold() == ".py":
        arguments = [str(launcher), *sys.argv[1:]]
    elif getattr(sys, "frozen", False):
        reset_name = "PYINSTALLER_RESET_ENVIRONMENT"
        previous_reset = os.environ.get(reset_name)
        os.environ[reset_name] = "1"
        try:
            result = QProcess.startDetached(
                sys.executable, sys.argv[1:], str(Path.cwd())
            )
        finally:
            if previous_reset is None:
                os.environ.pop(reset_name, None)
            else:
                os.environ[reset_name] = previous_reset
        return result[0] if isinstance(result, tuple) else bool(result)
    else:
        arguments = ["-m", "coding_agent.gui", *sys.argv[1:]]
    result = QProcess.startDetached(sys.executable, arguments, str(Path.cwd()))
    return result[0] if isinstance(result, tuple) else bool(result)


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, language: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._language = language
        self.setWindowTitle(tr(language, "settings_title"))
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.language_combo = QComboBox()
        self.language_combo.addItem(tr(language, "chinese"), "zh")
        self.language_combo.addItem(tr(language, "english"), "en")
        self.language_combo.setCurrentIndex(
            max(0, self.language_combo.findData(settings.language))
        )
        self.model_combo = QComboBox()
        self.model_combo.addItems(MODELS)
        self.model_combo.setCurrentText(settings.model)
        self.effort_combo = QComboBox()
        self.effort_combo.addItems(REASONING_EFFORTS)
        self.effort_combo.setCurrentText(settings.reasoning_effort)
        self.steps_spin = QSpinBox()
        self.steps_spin.setRange(1, 200)
        self.steps_spin.setValue(settings.max_steps)
        form.addRow(tr(language, "language"), self.language_combo)
        form.addRow(tr(language, "default_model"), self.model_combo)
        form.addRow(tr(language, "default_reasoning"), self.effort_combo)
        form.addRow(tr(language, "default_max_steps"), self.steps_spin)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText(tr(language, "save"))
        buttons.button(QDialogButtonBox.Cancel).setText(tr(language, "cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def values(self) -> AppSettings:
        return AppSettings(
            language=str(self.language_combo.currentData()),
            model=self.model_combo.currentText(),
            reasoning_effort=self.effort_combo.currentText(),
            max_steps=self.steps_spin.value(),
        )


class MainWindow(QMainWindow):
    def __init__(self, store: SessionStore | None = None) -> None:
        super().__init__()
        self.store = store or SessionStore()
        self.settings_store = SettingsStore(self.store.root)
        self.settings = self.settings_store.load()
        self.language = self.settings.language
        self.task_manager = AgentTaskManager(self)
        self.task_manager.event_received.connect(self._handle_session_event)
        self.task_manager.completed.connect(self._task_completed)
        self.task_manager.failed.connect(self._task_failed)
        self.task_manager.finished.connect(self._task_finished)
        self.current_session: Session | None = None
        self.failed_session_ids: set[str] = set()
        self.drafts: dict[str, tuple[str, list[str]]] = {}
        self._closing_after_tasks = False
        self._run_dots = 0
        self.run_timer = QTimer(self)
        self.run_timer.setInterval(450)
        self.run_timer.timeout.connect(self._advance_run_indicator)
        self.attachments: list[str] = []
        self.setWindowTitle("Coding Agent")
        self.resize(1240, 820)
        self.setMinimumSize(900, 620)
        self._build_ui()
        self._apply_style()
        self.refresh_sessions()

    def _build_ui(self) -> None:
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(1)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(14, 18, 14, 14)
        brand = QLabel("CODING AGENT")
        brand.setObjectName("brand")
        self.new_button = QPushButton(tr(self.language, "new_conversation"))
        self.new_button.setObjectName("primaryButton")
        self.new_button.clicked.connect(self.new_session)
        self.search_input = QLineEdit()
        self.search_input.setObjectName("sessionSearch")
        self.search_input.setPlaceholderText(tr(self.language, "search_conversations"))
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self._filter_sessions)
        self.session_list = QListWidget()
        self.session_list.setObjectName("sessionList")
        self.session_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.session_list.setTextElideMode(Qt.ElideRight)
        self.session_list.currentItemChanged.connect(self._session_selected)
        self.session_list.itemSelectionChanged.connect(self._sync_session_rows)
        self.settings_button = QPushButton(tr(self.language, "settings"))
        self.settings_button.clicked.connect(self.open_settings)
        side_layout.addWidget(brand)
        side_layout.addWidget(self.new_button)
        side_layout.addWidget(self.search_input)
        side_layout.addSpacing(4)
        side_layout.addWidget(self.session_list, 1)
        side_layout.addWidget(self.settings_button)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("header")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(24, 13, 24, 12)
        self.title_label = QLabel(tr(self.language, "start_conversation"))
        self.title_label.setObjectName("title")
        self.workspace_label = QLabel(tr(self.language, "choose_workspace_to_begin"))
        self.workspace_label.setObjectName("muted")
        header_layout.addWidget(self.title_label)
        header_layout.addWidget(self.workspace_label)

        self.conversation = ConversationView(self.language)

        composer = QFrame()
        composer.setObjectName("composer")
        composer_layout = QVBoxLayout(composer)
        composer_layout.setContentsMargins(20, 12, 20, 16)
        composer_layout.setSpacing(8)
        self.workspace_button = QPushButton(tr(self.language, "workspace_unselected"))
        self.workspace_button.setObjectName("workspaceButton")
        self.workspace_button.clicked.connect(self.change_workspace)
        self.editor = QTextEdit()
        self.editor.setPlaceholderText(tr(self.language, "input_placeholder"))
        self.editor.setAcceptRichText(False)
        self.editor.setMinimumHeight(92)
        self.editor.setMaximumHeight(180)
        controls = QHBoxLayout()
        self.attach_button = QPushButton(tr(self.language, "attach_files"))
        self.attach_button.clicked.connect(self.attach_files)
        self.attachment_label = QLabel("")
        self.attachment_label.setObjectName("muted")
        self.model_combo = QComboBox()
        self.model_combo.addItems(MODELS)
        self.model_combo.setCurrentText(self.settings.model)
        self.model_combo.currentTextChanged.connect(self._settings_changed)
        self.effort_combo = QComboBox()
        self.effort_combo.addItems(REASONING_EFFORTS)
        self.effort_combo.setCurrentText(self.settings.reasoning_effort)
        self.effort_combo.currentTextChanged.connect(self._settings_changed)
        self.subagent_label = QLabel(tr(self.language, "subagents"))
        self.subagent_toggle = ToggleSwitch()
        self.subagent_toggle.setAccessibleName(tr(self.language, "subagents"))
        self.subagent_toggle.setToolTip(tr(self.language, "subagents_tooltip"))
        self.subagent_toggle.toggled.connect(self._subagents_changed)
        self.stop_button = QPushButton(tr(self.language, "stop"))
        self.stop_button.setObjectName("stopButton")
        self.stop_button.setVisible(False)
        self.stop_button.clicked.connect(self.stop_run)
        self.send_button = QPushButton(tr(self.language, "send"))
        self.send_button.setObjectName("primaryButton")
        self.send_button.clicked.connect(self.send_message)
        controls.addWidget(self.attach_button)
        controls.addWidget(self.attachment_label, 1)
        controls.addWidget(QLabel(tr(self.language, "model")))
        controls.addWidget(self.model_combo)
        controls.addWidget(QLabel(tr(self.language, "reasoning_effort")))
        controls.addWidget(self.effort_combo)
        controls.addWidget(self.subagent_label)
        controls.addWidget(self.subagent_toggle)
        controls.addWidget(self.stop_button)
        controls.addWidget(self.send_button)
        composer_layout.addWidget(self.workspace_button, alignment=Qt.AlignLeft)
        composer_layout.addWidget(self.editor)
        composer_layout.addLayout(controls)

        right_layout.addWidget(header)
        right_layout.addWidget(self.conversation, 1)
        right_layout.addWidget(composer)
        splitter.addWidget(sidebar)
        splitter.addWidget(right)
        splitter.setSizes([260, 980])
        splitter.setStretchFactor(1, 1)
        self.setCentralWidget(splitter)

        send_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
        send_shortcut.activated.connect(self.send_message)
        new_shortcut = QShortcut(QKeySequence.New, self)
        new_shortcut.activated.connect(self.new_session)
        settings_shortcut = QShortcut(QKeySequence("Ctrl+,"), self)
        settings_shortcut.activated.connect(self.open_settings)

    def refresh_sessions(
        self,
        select_id: str | None = None,
        *,
        reload_selected: bool = True,
    ) -> None:
        current_id = select_id or (self.current_session.id if self.current_session else None)
        query = self.search_input.text().strip().casefold()
        all_sessions = self.store.list()
        sessions = [
            session
            for session in all_sessions
            if not query
            or query in session.title.casefold()
            or query in session.workspace.casefold()
            or query in Path(session.workspace).name.casefold()
        ]
        self.session_list.blockSignals(True)
        self.session_list.clear()
        selected: QListWidgetItem | None = None
        for session in sessions:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, session.id)
            item.setToolTip(session.workspace)
            self.session_list.addItem(item)
            row = SessionRow(session, self.language)
            row.clicked.connect(lambda target=item: self.session_list.setCurrentItem(target))
            row.menu_requested.connect(
                lambda position, session_id=session.id: self._show_session_menu(
                    session_id, position
                )
            )
            running = (
                "." * self._run_dots
                if self.task_manager.is_running(session.id) and self._run_dots
                else ""
            )
            row.set_session(
                session,
                running_text=running,
                failed=session.id in self.failed_session_ids,
            )
            item.setSizeHint(row.sizeHint())
            self.session_list.setItemWidget(item, row)
            if session.id == current_id:
                selected = item
        if selected:
            self.session_list.setCurrentItem(selected)
        elif self.session_list.count() and reload_selected:
            self.session_list.setCurrentRow(0)
            selected = self.session_list.currentItem()
        self.session_list.blockSignals(False)
        self._sync_session_rows()
        if selected and reload_selected:
            self.load_session(selected.data(Qt.UserRole))
        elif not all_sessions:
            self._show_empty_state()

    def _filter_sessions(self) -> None:
        self.refresh_sessions(reload_selected=False)

    def _sync_session_rows(self) -> None:
        for index in range(self.session_list.count()):
            item = self.session_list.item(index)
            row = self.session_list.itemWidget(item)
            if isinstance(row, SessionRow):
                row.set_selected(item.isSelected())

    def new_session(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self, tr(self.language, "choose_workspace"), str(Path.cwd())
        )
        if not directory:
            return
        if not self._confirm_workspace_access(directory):
            return
        session = self.store.create(
            workspace=directory,
            title=tr(self.language, "new_session_title"),
            model=self.settings.model,
            reasoning_effort=self.settings.reasoning_effort,
            preferred_language=self.language,
        )
        self.refresh_sessions(session.id)
        self.editor.setFocus()

    def delete_session(self, session_id: str | None = None) -> None:
        target_id = session_id or (self.current_session.id if self.current_session else None)
        if not target_id:
            return
        if self.task_manager.is_running(target_id):
            return
        session = self.store.load(target_id)
        answer = QMessageBox.question(
            self,
            tr(self.language, "delete_title"),
            tr(self.language, "delete_question", title=session.title),
        )
        if answer == QMessageBox.Yes:
            self.store.delete(target_id)
            if self.current_session and self.current_session.id == target_id:
                self.current_session = None
            self.refresh_sessions()

    def rename_session(self, session_id: str) -> None:
        session = self.store.load(session_id)
        title, accepted = QInputDialog.getText(
            self,
            tr(self.language, "rename_title"),
            tr(self.language, "rename_label"),
            text=session.title,
        )
        if not accepted:
            return
        title = title.strip()
        if not title:
            QMessageBox.warning(
                self,
                tr(self.language, "rename_empty_title"),
                tr(self.language, "rename_empty_message"),
            )
            return
        self._rename_session(session_id, title)

    def _rename_session(self, session_id: str, title: str) -> None:
        title = title.strip()
        if not title:
            raise ValueError("Session title must not be empty")
        session = self.store.update_metadata(session_id, title=title)
        if self.current_session and self.current_session.id == session_id:
            self.current_session = session
            self.title_label.setText(session.title)
        self.refresh_sessions(session_id, reload_selected=False)

    def toggle_session_pinned(self, session_id: str) -> None:
        session = self.store.load(session_id)
        session = self.store.update_metadata(session_id, pinned=not session.pinned)
        if self.current_session and self.current_session.id == session_id:
            self.current_session = session
        self.refresh_sessions(session_id, reload_selected=False)

    def set_session_unread(self, session_id: str, unread: bool) -> None:
        session = self.store.update_metadata(session_id, unread=unread)
        if self.current_session and self.current_session.id == session_id:
            self.current_session = session
        self.refresh_sessions(session_id, reload_selected=False)

    def open_session_workspace(self, session_id: str) -> None:
        session = self.store.load(session_id)
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(session.workspace)):
            QMessageBox.warning(
                self,
                tr(self.language, "open_workspace_failed_title"),
                tr(
                    self.language,
                    "open_workspace_failed_message",
                    workspace=session.workspace,
                ),
            )

    def _show_session_menu(self, session_id: str, position) -> None:
        session = self.store.load(session_id)
        menu = QMenu(self)
        stop_action = None
        if self.task_manager.is_running(session_id):
            stop_action = menu.addAction(tr(self.language, "stop_task"))
            menu.addSeparator()
        rename_action = menu.addAction(tr(self.language, "rename"))
        pin_action = menu.addAction(tr(self.language, "unpin" if session.pinned else "pin"))
        unread_action = menu.addAction(
            tr(self.language, "mark_read" if session.unread else "mark_unread")
        )
        open_action = menu.addAction(tr(self.language, "open_workspace"))
        menu.addSeparator()
        delete_action = menu.addAction(tr(self.language, "delete_conversation"))
        delete_action.setEnabled(not self.task_manager.is_running(session_id))
        chosen = menu.exec(position)
        if stop_action is not None and chosen == stop_action:
            self.task_manager.stop(session_id)
        elif chosen == rename_action:
            self.rename_session(session_id)
        elif chosen == pin_action:
            self.toggle_session_pinned(session_id)
        elif chosen == unread_action:
            self.set_session_unread(session_id, not session.unread)
        elif chosen == open_action:
            self.open_session_workspace(session_id)
        elif chosen == delete_action:
            self.delete_session(session_id)

    def load_session(self, session_id: str) -> None:
        if self.current_session:
            self.drafts[self.current_session.id] = (
                self.editor.toPlainText(),
                list(self.attachments),
            )
        self.current_session = self.store.load(session_id)
        session = self.current_session
        metadata_changes: dict[str, object] = {}
        if session.preferred_language != self.language:
            metadata_changes["preferred_language"] = self.language
        if session.unread:
            metadata_changes["unread"] = False
        self.failed_session_ids.discard(session.id)
        if metadata_changes:
            session = self.store.update_metadata(session.id, **metadata_changes)
            self.current_session = session
        self.title_label.setText(session.title)
        self.workspace_label.setText(session.workspace)
        self.workspace_button.setText(
            tr(self.language, "workspace_value", workspace=session.workspace)
        )
        self.model_combo.blockSignals(True)
        self.effort_combo.blockSignals(True)
        self.model_combo.setCurrentText(session.model)
        self.effort_combo.setCurrentText(session.reasoning_effort)
        self.subagent_toggle.blockSignals(True)
        self.subagent_toggle.setChecked(session.subagents_enabled)
        self.subagent_toggle.blockSignals(False)
        self.model_combo.blockSignals(False)
        self.effort_combo.blockSignals(False)
        draft_text, draft_attachments = self.drafts.get(session.id, ("", []))
        self.editor.setPlainText(draft_text)
        self.attachments = list(draft_attachments)
        self._update_attachments()
        self.conversation.render(session.transcript)
        self._sync_controls()
        if metadata_changes:
            self.refresh_sessions(session.id, reload_selected=False)

    def change_workspace(self) -> None:
        if self.current_session and self.task_manager.is_running(self.current_session.id):
            return
        initial = self.current_session.workspace if self.current_session else str(Path.cwd())
        directory = QFileDialog.getExistingDirectory(
            self, tr(self.language, "choose_workspace"), initial
        )
        if not directory:
            return
        if self.current_session and Path(directory).resolve() == Path(self.current_session.workspace):
            return
        if not self._confirm_workspace_access(directory):
            return
        if self.current_session and self.current_session.transcript:
            answer = QMessageBox.warning(
                self,
                tr(self.language, "change_workspace_title"),
                tr(self.language, "change_workspace_message"),
                QMessageBox.Yes | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            if answer != QMessageBox.Yes:
                return
            session = self.store.create(
                workspace=directory,
                title=tr(self.language, "new_session_title"),
                model=self.settings.model,
                reasoning_effort=self.settings.reasoning_effort,
                preferred_language=self.language,
            )
        elif self.current_session:
            self.current_session.workspace = str(Path(directory).resolve())
            self.current_session.model_context = None
            self.store.save(self.current_session)
            session = self.current_session
        else:
            session = self.store.create(
                workspace=directory,
                title=tr(self.language, "new_session_title"),
                model=self.settings.model,
                reasoning_effort=self.settings.reasoning_effort,
                preferred_language=self.language,
            )
        self.refresh_sessions(session.id)

    def _confirm_workspace_access(self, directory: str) -> bool:
        workspace = str(Path(directory).expanduser().resolve())
        prompt = QMessageBox(self)
        prompt.setIcon(QMessageBox.Warning)
        prompt.setWindowTitle(tr(self.language, "workspace_authorization_title"))
        prompt.setText(
            tr(
                self.language,
                "workspace_authorization_message",
                workspace=workspace,
            )
        )
        cancel = prompt.addButton(tr(self.language, "cancel"), QMessageBox.RejectRole)
        allow = prompt.addButton(
            tr(self.language, "allow_workspace"), QMessageBox.AcceptRole
        )
        prompt.exec()
        return prompt.clickedButton() == allow and prompt.clickedButton() != cancel

    def _confirm_same_workspace_concurrency(self) -> bool:
        prompt = QMessageBox(self)
        prompt.setIcon(QMessageBox.Warning)
        prompt.setWindowTitle(tr(self.language, "same_workspace_title"))
        prompt.setText(tr(self.language, "same_workspace_message"))
        cancel = prompt.addButton(tr(self.language, "cancel"), QMessageBox.RejectRole)
        proceed = prompt.addButton(
            tr(self.language, "continue_running"), QMessageBox.AcceptRole
        )
        prompt.exec()
        return prompt.clickedButton() == proceed and prompt.clickedButton() != cancel

    def _confirm_stop_all(self, count: int) -> bool:
        prompt = QMessageBox(self)
        prompt.setIcon(QMessageBox.Warning)
        prompt.setWindowTitle(tr(self.language, "tasks_running_title"))
        prompt.setText(
            tr(self.language, "tasks_running_message", count=count)
        )
        cancel = prompt.addButton(tr(self.language, "cancel"), QMessageBox.RejectRole)
        stop_all = prompt.addButton(tr(self.language, "stop_all"), QMessageBox.AcceptRole)
        prompt.exec()
        return prompt.clickedButton() == stop_all and prompt.clickedButton() != cancel

    def attach_files(self) -> None:
        if not self.current_session:
            QMessageBox.information(
                self,
                tr(self.language, "choose_workspace"),
                tr(self.language, "create_first"),
            )
            return
        files, _ = QFileDialog.getOpenFileNames(
            self,
            tr(self.language, "attach_title"),
            self.current_session.workspace,
            tr(self.language, "text_files"),
        )
        root = Path(self.current_session.workspace).resolve()
        for raw in files:
            source = Path(raw).resolve()
            try:
                source.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                QMessageBox.warning(
                    self,
                    tr(self.language, "attachment_rejected"),
                    f"{source.name}: {exc}",
                )
                continue
            if source.is_relative_to(root):
                relative = source.relative_to(root).as_posix()
            else:
                answer = QMessageBox.question(
                    self,
                    tr(self.language, "copy_title"),
                    tr(self.language, "copy_message", name=source.name),
                )
                if answer != QMessageBox.Yes:
                    continue
                destination_dir = root / ".agent-attachments"
                destination_dir.mkdir(exist_ok=True)
                destination = self._available_destination(destination_dir, source.name)
                shutil.copy2(source, destination)
                relative = destination.relative_to(root).as_posix()
            if relative not in self.attachments:
                self.attachments.append(relative)
        self._update_attachments()

    def send_message(self) -> None:
        if not self.current_session:
            QMessageBox.information(
                self,
                tr(self.language, "choose_workspace"),
                tr(self.language, "create_first"),
            )
            return
        session_id = self.current_session.id
        if self.task_manager.is_running(session_id):
            return
        message = self.editor.toPlainText().strip()
        if not message:
            return
        conflicts = self._same_workspace_running_sessions(self.current_session)
        if conflicts and not self._confirm_same_workspace_concurrency():
            return
        attachments = list(self.attachments)
        self.conversation.add_user(message, attachments)
        self.editor.clear()
        self.attachments.clear()
        self.drafts[session_id] = ("", [])
        self._update_attachments()
        self.failed_session_ids.discard(session_id)
        runtime = SessionRuntime(
            self.store,
            language=self.language,
            max_steps=self.settings.max_steps,
        )
        self.task_manager.start(
            runtime=runtime,
            session_id=session_id,
            message=message,
            attachments=attachments,
        )
        if not self.run_timer.isActive():
            self._run_dots = 1
            self.run_timer.start()
        self._update_session_row(session_id)
        self._sync_controls()

    def stop_run(self) -> None:
        if self.current_session and self.task_manager.stop(self.current_session.id):
            self.stop_button.setText(tr(self.language, "stopping"))
            self.stop_button.setEnabled(False)

    def _handle_session_event(self, session_id: str, event: AgentEvent) -> None:
        if not self.current_session or self.current_session.id != session_id:
            if event.kind == "persistence_warning":
                self._mark_background_unread(session_id, failed=False)
            return
        if event.kind == "reasoning_delta":
            self.conversation.append_reasoning(event.message)
        elif event.kind in {"content_delta", "model"}:
            self.conversation.append_assistant(event.message)
        elif event.kind == "tool_call":
            self.conversation.add_tool(event.tool_name or "tool", event.message, event.call_id)
        elif event.kind == "tool_result":
            self.conversation.finish_tool(
                event.tool_name or "tool", event.message, bool(event.ok), event.call_id
            )
        elif event.kind == "final":
            self.conversation.finish_assistant(event.message)
        elif event.kind in {
            "verification",
            "stopped",
            "persistence_warning",
            "persistence_recovered",
        }:
            self.conversation.add_event_status(event.kind, event.message, event.ok)

    def _task_completed(self, session_id: str, result: AgentResult) -> None:
        if not self.current_session or self.current_session.id != session_id:
            self._mark_background_unread(session_id, failed=False)

    def _task_failed(self, session_id: str, category: str, message: str) -> None:
        is_current = self.current_session is not None and self.current_session.id == session_id
        if is_current:
            self.conversation.add_status(message, False)
            title_key = (
                "session_persistence_failed"
                if category == "persistence"
                else "agent_failed"
            )
            QMessageBox.critical(self, tr(self.language, title_key), message)
        else:
            self._mark_background_unread(session_id, failed=True)

    def _task_finished(self, session_id: str) -> None:
        if not self.task_manager.running_count():
            self.run_timer.stop()
            self._run_dots = 0
        self.refresh_sessions(
            self.current_session.id if self.current_session else None,
            reload_selected=False,
        )
        self._sync_controls()
        if self._closing_after_tasks and not self.task_manager.running_count():
            QTimer.singleShot(0, self.close)

    def _mark_background_unread(self, session_id: str, *, failed: bool) -> None:
        try:
            self.store.update_metadata(session_id, unread=True)
        except (KeyError, SessionPersistenceError):
            pass
        if failed:
            self.failed_session_ids.add(session_id)
        self._update_session_row(session_id)

    def _same_workspace_running_sessions(self, session: Session) -> list[str]:
        workspace = str(Path(session.workspace).resolve()).casefold()
        conflicts: list[str] = []
        for session_id in self.task_manager.running_session_ids():
            if session_id == session.id:
                continue
            try:
                other = self.store.load(session_id)
            except (KeyError, SessionPersistenceError, ValueError):
                continue
            if str(Path(other.workspace).resolve()).casefold() == workspace:
                conflicts.append(session_id)
        return conflicts

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self.language, self)
        if dialog.exec() != QDialog.Accepted:
            return
        self._apply_settings(dialog.values())

    def _apply_settings(self, updated: AppSettings) -> None:
        language_changed = updated.language != self.language
        self.settings_store.save(updated)
        self.settings = updated
        if language_changed:
            running_count = self.task_manager.running_count()
            if running_count:
                QMessageBox.information(
                    self,
                    tr(self.language, "restart_tasks_running_title"),
                    tr(
                        self.language,
                        "restart_tasks_running_message",
                        count=running_count,
                    ),
                )
            elif self._confirm_restart():
                self._restart_application()

    def _confirm_restart(self) -> bool:
        prompt = QMessageBox(self)
        prompt.setIcon(QMessageBox.Question)
        prompt.setWindowTitle(tr(self.language, "restart_title"))
        prompt.setText(tr(self.language, "restart_message"))
        later = prompt.addButton(tr(self.language, "restart_later"), QMessageBox.RejectRole)
        restart = prompt.addButton(tr(self.language, "restart_now"), QMessageBox.AcceptRole)
        prompt.exec()
        return prompt.clickedButton() == restart and prompt.clickedButton() != later

    def _restart_application(self) -> bool:
        if not start_replacement_gui():
            QMessageBox.warning(
                self,
                tr(self.language, "restart_failed_title"),
                tr(self.language, "restart_failed_message"),
            )
            return False
        self.close()
        return True

    def _settings_changed(self) -> None:
        if (
            not self.current_session
            or self.task_manager.is_running(self.current_session.id)
        ):
            return
        self.current_session = self.store.update_metadata(
            self.current_session.id,
            model=self.model_combo.currentText(),
            reasoning_effort=self.effort_combo.currentText(),
        )

    def _subagents_changed(self, enabled: bool) -> None:
        if (
            not self.current_session
            or self.task_manager.is_running(self.current_session.id)
        ):
            return
        self.current_session = self.store.update_metadata(
            self.current_session.id,
            subagents_enabled=enabled,
        )

    def _session_selected(self, current: QListWidgetItem | None) -> None:
        if current:
            self.load_session(current.data(Qt.UserRole))

    def _show_empty_state(self) -> None:
        if self.current_session:
            self.drafts[self.current_session.id] = (
                self.editor.toPlainText(),
                list(self.attachments),
            )
        self.current_session = None
        self.title_label.setText(tr(self.language, "start_conversation"))
        self.workspace_label.setText(tr(self.language, "choose_workspace_to_begin"))
        self.workspace_button.setText(tr(self.language, "workspace_unselected"))
        self.conversation.clear_transcript()
        self.conversation.add_status(tr(self.language, "empty_status"))
        self.editor.clear()
        self.attachments.clear()
        self.subagent_toggle.blockSignals(True)
        self.subagent_toggle.setChecked(False)
        self.subagent_toggle.blockSignals(False)
        self._update_attachments()
        self._sync_controls()

    def _sync_controls(self) -> None:
        has_session = self.current_session is not None
        running = bool(
            self.current_session
            and self.task_manager.is_running(self.current_session.id)
        )
        self.editor.setEnabled(has_session)
        self.workspace_button.setEnabled(has_session and not running)
        self.attach_button.setEnabled(has_session and not running)
        self.model_combo.setEnabled(has_session and not running)
        self.effort_combo.setEnabled(has_session and not running)
        self.subagent_toggle.setEnabled(has_session and not running)
        self.send_button.setEnabled(has_session and not running)
        self.new_button.setEnabled(True)
        self.session_list.setEnabled(True)
        self.search_input.setEnabled(True)
        self.settings_button.setEnabled(True)
        self.stop_button.setVisible(running)
        self.stop_button.setEnabled(running)
        self.stop_button.setText(tr(self.language, "stop"))
        self.send_button.setVisible(not running)

    def _advance_run_indicator(self) -> None:
        running_ids = self.task_manager.running_session_ids()
        if not running_ids:
            self.run_timer.stop()
            return
        self._run_dots = self._run_dots % 3 + 1
        for session_id in running_ids:
            self._update_session_row(session_id)

    def _update_session_row(self, session_id: str) -> None:
        try:
            session = self.store.load(session_id)
        except (KeyError, SessionPersistenceError, ValueError):
            return
        for index in range(self.session_list.count()):
            item = self.session_list.item(index)
            if item.data(Qt.UserRole) != session_id:
                continue
            row = self.session_list.itemWidget(item)
            if isinstance(row, SessionRow):
                running = (
                    "." * self._run_dots
                    if self.task_manager.is_running(session_id) and self._run_dots
                    else ""
                )
                row.set_session(
                    session,
                    running_text=running,
                    failed=session_id in self.failed_session_ids,
                )
            break

    def _update_attachments(self) -> None:
        self.attachment_label.setText(
            tr(
                self.language,
                "file_count",
                count=len(self.attachments),
                names=", ".join(Path(p).name for p in self.attachments),
            )
            if self.attachments
            else ""
        )

    @staticmethod
    def _available_destination(directory: Path, name: str) -> Path:
        candidate = directory / name
        stem, suffix = candidate.stem, candidate.suffix
        counter = 2
        while candidate.exists():
            candidate = directory / f"{stem}-{counter}{suffix}"
            counter += 1
        return candidate

    def closeEvent(self, event: QCloseEvent) -> None:
        running_count = self.task_manager.running_count()
        if running_count:
            if not self._closing_after_tasks and self._confirm_stop_all(running_count):
                self._closing_after_tasks = True
                self.task_manager.stop_all()
            event.ignore()
            return
        event.accept()

    def _apply_style(self) -> None:
        self.setStyleSheet(STYLE)


STYLE = """
QWidget { background: #111318; color: #d8dee9; font-family: "Segoe UI"; font-size: 13px; }
QLabel { background: transparent; }
#sidebar { background: #171a21; border-right: 1px solid #292e39; }
#brand { color: #7f8ca5; font-size: 11px; font-weight: 700; letter-spacing: 2px; padding: 2px 4px 10px; }
#header { background: #14171d; border-bottom: 1px solid #292e39; }
#title { color: #f1f4f8; font-size: 17px; font-weight: 650; }
#muted { color: #7f8a9d; font-size: 11px; }
#composer { background: #14171d; border-top: 1px solid #292e39; }
QPushButton { background: #20242d; border: 1px solid #333947; border-radius: 6px; padding: 7px 11px; }
QPushButton:hover { background: #292e39; border-color: #465067; }
QPushButton:disabled { color: #5d6472; background: #191c22; }
#primaryButton { background: #3468d4; border-color: #477be4; color: white; font-weight: 600; }
#primaryButton:hover { background: #3d73e0; }
#stopButton { background: #40252a; border-color: #73404a; color: #ffb4bc; }
#workspaceButton { background: transparent; border: none; color: #96a9ca; padding: 2px; text-align: left; }
QTextEdit, QPlainTextEdit, QComboBox, QLineEdit { background: #191c23; border: 1px solid #303643; border-radius: 6px; selection-background-color: #355fba; }
QTextEdit { padding: 10px; font-size: 14px; }
QComboBox { padding: 6px 9px; min-width: 120px; }
#sessionSearch { padding: 7px 9px; margin: 2px 0 4px; }
QListWidget { background: transparent; border: none; outline: none; }
QListWidget::item { padding: 0; border-radius: 6px; margin: 2px 0; }
QListWidget::item:selected { background: #252b36; }
SessionRow { background: transparent; border-radius: 6px; }
SessionRow[hovered="true"] { background: #1e222b; }
SessionRow[selected="true"] { background: #252b36; }
#sessionTitle { color: #c5cedd; font-weight: 550; }
#sessionWorkspace { color: #747f92; font-size: 10px; }
#sessionMarker { color: #8baaf0; font-size: 11px; }
#sessionMenuButton { color: #aab5c6; font-weight: 700; padding: 2px; min-width: 24px; }
#userBubble { background: #294e91; border: 1px solid #3864ad; border-radius: 10px; }
#assistantBubble { background: #1a1e26; border: 1px solid #2d3441; border-radius: 10px; }
#bubbleHeading { color: #9eabc0; font-size: 10px; font-weight: 700; }
#detailCard { background: #171b22; border: 1px solid #2c3340; border-radius: 7px; min-width: 480px; }
#detailCard[failed="true"] { border-color: #6d3942; }
QToolButton { background: transparent; border: none; color: #aeb8c8; font-weight: 600; }
#statusLabel { color: #91a0b5; padding: 5px; }
#errorLabel { color: #ef9aa6; padding: 5px; }
QScrollBar:vertical { width: 10px; background: #111318; }
QScrollBar::handle:vertical { background: #343a47; border-radius: 5px; min-height: 24px; }
QSplitter::handle { background: #292e39; }
"""


def main() -> int:
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Coding Agent")
    window = MainWindow()
    window.show()
    return app.exec()
