"""Persistent PySide6 desktop application."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
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
from coding_agent.gui.settings import (
    MODELS,
    REASONING_EFFORTS,
    AppSettings,
    SettingsStore,
)
from coding_agent.gui.widgets import ConversationView
from coding_agent.gui.worker import AgentWorker
from coding_agent.session_runtime import SessionRuntime
from coding_agent.sessions import Session, SessionStore


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
        self.runtime = SessionRuntime(
            self.store,
            language=self.language,
            max_steps=self.settings.max_steps,
        )
        self.current_session: Session | None = None
        self.worker: AgentWorker | None = None
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
        self.session_list = QListWidget()
        self.session_list.setObjectName("sessionList")
        self.session_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.session_list.setTextElideMode(Qt.ElideRight)
        self.session_list.currentItemChanged.connect(self._session_selected)
        self.settings_button = QPushButton(tr(self.language, "settings"))
        self.settings_button.clicked.connect(self.open_settings)
        self.delete_button = QPushButton(tr(self.language, "delete_conversation"))
        self.delete_button.clicked.connect(self.delete_session)
        side_layout.addWidget(brand)
        side_layout.addWidget(self.new_button)
        side_layout.addSpacing(8)
        side_layout.addWidget(self.session_list, 1)
        side_layout.addWidget(self.settings_button)
        side_layout.addWidget(self.delete_button)

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

    def refresh_sessions(self, select_id: str | None = None) -> None:
        current_id = select_id or (self.current_session.id if self.current_session else None)
        self.session_list.blockSignals(True)
        self.session_list.clear()
        selected: QListWidgetItem | None = None
        for session in self.store.list():
            item = QListWidgetItem(f"{session.title}\n{Path(session.workspace).name}")
            item.setData(Qt.UserRole, session.id)
            item.setToolTip(session.workspace)
            self.session_list.addItem(item)
            if session.id == current_id:
                selected = item
        self.session_list.blockSignals(False)
        if selected:
            self.session_list.setCurrentItem(selected)
            self.load_session(selected.data(Qt.UserRole))
        elif self.session_list.count():
            self.session_list.setCurrentRow(0)
        else:
            self._show_empty_state()

    def new_session(self) -> None:
        if self.worker:
            return
        directory = QFileDialog.getExistingDirectory(
            self, tr(self.language, "choose_workspace"), str(Path.cwd())
        )
        if not directory:
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

    def delete_session(self) -> None:
        if not self.current_session or self.worker:
            return
        answer = QMessageBox.question(
            self,
            tr(self.language, "delete_title"),
            tr(self.language, "delete_question", title=self.current_session.title),
        )
        if answer == QMessageBox.Yes:
            self.store.delete(self.current_session.id)
            self.current_session = None
            self.refresh_sessions()

    def load_session(self, session_id: str) -> None:
        self.current_session = self.store.load(session_id)
        session = self.current_session
        if session.preferred_language != self.language:
            session.preferred_language = self.language
            self.store.save(session)
        self.title_label.setText(session.title)
        self.workspace_label.setText(session.workspace)
        self.workspace_button.setText(
            tr(self.language, "workspace_value", workspace=session.workspace)
        )
        self.model_combo.blockSignals(True)
        self.effort_combo.blockSignals(True)
        self.model_combo.setCurrentText(session.model)
        self.effort_combo.setCurrentText(session.reasoning_effort)
        self.model_combo.blockSignals(False)
        self.effort_combo.blockSignals(False)
        self.attachments.clear()
        self._update_attachments()
        self.conversation.render(session.transcript)
        self._set_controls_enabled(True)

    def change_workspace(self) -> None:
        if self.worker:
            return
        initial = self.current_session.workspace if self.current_session else str(Path.cwd())
        directory = QFileDialog.getExistingDirectory(
            self, tr(self.language, "choose_workspace"), initial
        )
        if not directory:
            return
        if self.current_session and Path(directory).resolve() == Path(self.current_session.workspace):
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

    def attach_files(self) -> None:
        if not self.current_session or self.worker:
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
        if self.worker:
            return
        if not self.current_session:
            QMessageBox.information(
                self,
                tr(self.language, "choose_workspace"),
                tr(self.language, "create_first"),
            )
            return
        message = self.editor.toPlainText().strip()
        if not message:
            return
        attachments = list(self.attachments)
        self.conversation.add_user(message, attachments)
        self.editor.clear()
        self.attachments.clear()
        self._update_attachments()
        self.worker = AgentWorker(
            runtime=self.runtime,
            session_id=self.current_session.id,
            message=message,
            attachments=attachments,
        )
        self.worker.event_received.connect(self._handle_event)
        self.worker.completed.connect(self._run_completed)
        self.worker.failed.connect(self._run_failed)
        self.worker.finished.connect(self._worker_finished)
        self._set_running(True)
        self.worker.start()

    def stop_run(self) -> None:
        if self.worker:
            self.stop_button.setText(tr(self.language, "stopping"))
            self.stop_button.setEnabled(False)
            self.worker.request_stop()

    def _handle_event(self, event: AgentEvent) -> None:
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
        elif event.kind in {"verification", "stopped"}:
            self.conversation.add_event_status(event.kind, event.message, event.ok)

    def _run_completed(self, result: AgentResult) -> None:
        if result.status != "completed" and result.status != "cancelled":
            self.conversation.add_event_status("stopped", result.final_answer, False)

    def _run_failed(self, message: str) -> None:
        self.conversation.add_status(message, False)
        QMessageBox.critical(self, tr(self.language, "agent_failed"), message)

    def _worker_finished(self) -> None:
        session_id = self.current_session.id if self.current_session else None
        worker = self.worker
        self.worker = None
        if worker:
            worker.deleteLater()
        self._set_running(False)
        if session_id:
            self.refresh_sessions(session_id)

    def open_settings(self) -> None:
        if self.worker:
            return
        dialog = SettingsDialog(self.settings, self.language, self)
        if dialog.exec() != QDialog.Accepted:
            return
        updated = dialog.values()
        language_changed = updated.language != self.language
        self.settings_store.save(updated)
        self.settings = updated
        self.runtime = SessionRuntime(
            self.store,
            language=self.language,
            max_steps=updated.max_steps,
        )
        if language_changed:
            QMessageBox.information(
                self,
                tr(self.language, "restart_title"),
                tr(self.language, "restart_message"),
            )

    def _settings_changed(self) -> None:
        if not self.current_session or self.worker:
            return
        self.current_session.model = self.model_combo.currentText()
        self.current_session.reasoning_effort = self.effort_combo.currentText()
        self.store.save(self.current_session)

    def _session_selected(self, current: QListWidgetItem | None) -> None:
        if current and not self.worker:
            self.load_session(current.data(Qt.UserRole))

    def _show_empty_state(self) -> None:
        self.current_session = None
        self.title_label.setText(tr(self.language, "start_conversation"))
        self.workspace_label.setText(tr(self.language, "choose_workspace_to_begin"))
        self.workspace_button.setText(tr(self.language, "workspace_unselected"))
        self.conversation.clear_transcript()
        self.conversation.add_status(tr(self.language, "empty_status"))
        self._set_controls_enabled(True)

    def _set_running(self, running: bool) -> None:
        self._set_controls_enabled(not running)
        self.stop_button.setVisible(running)
        self.stop_button.setEnabled(running)
        self.stop_button.setText(tr(self.language, "stop"))
        self.send_button.setVisible(not running)

    def _set_controls_enabled(self, enabled: bool) -> None:
        has_session = enabled and self.current_session is not None
        for widget in (
            self.editor,
            self.workspace_button,
            self.attach_button,
            self.model_combo,
            self.effort_combo,
            self.send_button,
            self.delete_button,
            self.settings_button,
            self.session_list,
            self.new_button,
        ):
            widget.setEnabled(
                has_session
                if widget not in {self.new_button, self.session_list, self.settings_button}
                else enabled
            )

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
        if self.worker:
            self.stop_run()
            QMessageBox.information(
                self,
                tr(self.language, "stop_title"),
                tr(self.language, "stop_message"),
            )
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
QTextEdit, QPlainTextEdit, QComboBox { background: #191c23; border: 1px solid #303643; border-radius: 6px; selection-background-color: #355fba; }
QTextEdit { padding: 10px; font-size: 14px; }
QComboBox { padding: 6px 9px; min-width: 120px; }
QListWidget { background: transparent; border: none; outline: none; }
QListWidget::item { padding: 10px 9px; border-radius: 6px; margin: 2px 0; color: #b7c0cf; }
QListWidget::item:selected { background: #252b36; color: white; }
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
