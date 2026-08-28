"""Small transcript widgets with collapsible reasoning and tool details."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QResizeEvent, QTextCursor
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from coding_agent.gui.i18n import tr


class MessageBubble(QFrame):
    def __init__(self, role: str, text: str = "", language: str = "zh") -> None:
        super().__init__()
        self.setObjectName("userBubble" if role == "user" else "assistantBubble")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(14, 10, 14, 10)
        heading = QLabel(tr(language, "you") if role == "user" else tr(language, "agent"))
        heading.setObjectName("bubbleHeading")
        self.body = QLabel(text)
        self.body.setWordWrap(True)
        self.body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.body.setTextFormat(Qt.PlainText if role == "user" else Qt.MarkdownText)
        self.body.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        self._layout.addWidget(heading)
        self._layout.addWidget(self.body)
        self.setMaximumWidth(860)
        if role != "user":
            self.setMinimumWidth(520)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        self._sync_height()

    def append_text(self, delta: str) -> None:
        self.body.setText(self.body.text() + delta)
        self._sync_height()

    def set_text(self, text: str) -> None:
        self.body.setText(text)
        self._sync_height()

    def _sync_height(self) -> None:
        width = self.body.width()
        if width < 40:
            width = 820 if self.objectName() == "assistantBubble" else 520
        wrapped_height = self.body.heightForWidth(width)
        content_height = max(
            self.body.sizeHint().height(),
            wrapped_height if wrapped_height > 0 else 0,
        )
        self.body.setFixedHeight(content_height)
        self.body.updateGeometry()
        self._layout.activate()
        self.updateGeometry()
        self.setMinimumHeight(max(54, self.sizeHint().height()))

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        QTimer.singleShot(0, self._sync_height)


class CollapsibleCard(QFrame):
    def __init__(self, title: str, text: str = "", *, expanded: bool = False) -> None:
        super().__init__()
        self.setObjectName("detailCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 8)
        self.toggle = QToolButton()
        self.toggle.setText(title)
        self.toggle.setCheckable(True)
        self.toggle.setChecked(expanded)
        self.toggle.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self.toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.detail = QPlainTextEdit(text)
        self.detail.setReadOnly(True)
        self.detail.setVisible(expanded)
        self.detail.setMaximumHeight(260)
        self.detail.setFont(QFont("Consolas", 9))
        self.toggle.toggled.connect(self._toggle)
        layout.addWidget(self.toggle)
        layout.addWidget(self.detail)

    def _toggle(self, checked: bool) -> None:
        self.toggle.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)
        self.detail.setVisible(checked)

    def append_text(self, delta: str) -> None:
        cursor = self.detail.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(delta)
        self.detail.setTextCursor(cursor)


class ToolCard(CollapsibleCard):
    def __init__(
        self,
        name: str,
        summary: str,
        call_id: str | None = None,
        language: str = "zh",
    ) -> None:
        super().__init__(f"●  {name}  ·  {tr(language, 'running')}", summary)
        self.name = name
        self.call_id = call_id
        self.language = language

    def set_result(self, text: str, ok: bool) -> None:
        status = tr(self.language, "completed" if ok else "failed")
        marker = "✓" if ok else "×"
        self.toggle.setText(f"{marker}  {self.name}  ·  {status}")
        existing = self.detail.toPlainText()
        self.detail.setPlainText((existing + "\n\n" if existing else "") + text)
        self.setProperty("failed", not ok)
        self.style().unpolish(self)
        self.style().polish(self)


class ConversationView(QScrollArea):
    def __init__(self, language: str = "zh") -> None:
        super().__init__()
        self.language = language
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.container = QWidget()
        self.layout = QVBoxLayout(self.container)
        self.layout.setContentsMargins(24, 24, 24, 24)
        self.layout.setSpacing(12)
        self.layout.setAlignment(Qt.AlignTop)
        self.setWidget(self.container)
        self._reasoning: CollapsibleCard | None = None
        self._assistant: MessageBubble | None = None
        self._tools: dict[str, ToolCard] = {}

    def clear_transcript(self) -> None:
        while self.layout.count():
            item = self.layout.takeAt(0)
            if item.widget():
                widget = item.widget()
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        self._reasoning = None
        self._assistant = None
        self._tools = {}

    def add_item(self, widget: QWidget, *, align_right: bool = False) -> None:
        alignment = Qt.AlignRight if align_right else Qt.AlignLeft
        self.layout.addWidget(widget, 0, alignment)
        self._scroll_bottom()

    def add_user(self, text: str, attachments: list[str] | None = None) -> None:
        rendered = text
        if attachments:
            rendered += f"\n\n{tr(self.language, 'files')}: " + ", ".join(attachments)
        self.add_item(MessageBubble("user", rendered, self.language), align_right=True)
        self._reset_stream_targets()

    def append_reasoning(self, delta: str) -> None:
        if self._reasoning is None:
            self._reasoning = CollapsibleCard(tr(self.language, "reasoning"), "")
            self.add_item(self._reasoning)
        self._reasoning.append_text(delta)

    def append_assistant(self, delta: str) -> None:
        if self._assistant is None:
            self._assistant = MessageBubble("assistant", "", self.language)
            self.add_item(self._assistant)
        self._assistant.append_text(delta)

    def finish_assistant(self, text: str) -> None:
        if self._assistant is None:
            self._assistant = MessageBubble("assistant", text, self.language)
            self.add_item(self._assistant)
        else:
            self._assistant.set_text(text)
        self._reasoning = None
        self._assistant = None

    def add_tool(self, name: str, summary: str, call_id: str | None) -> None:
        card = ToolCard(name, summary, call_id, self.language)
        self.add_item(card)
        if call_id:
            self._tools[call_id] = card
        self._reasoning = None
        self._assistant = None

    def finish_tool(self, name: str, text: str, ok: bool, call_id: str | None) -> None:
        card = self._tools.get(call_id or "")
        if card is None:
            card = ToolCard(name, "", call_id, self.language)
            self.add_item(card)
        card.set_result(text, ok)

    def add_status(self, text: str, ok: bool | None = None) -> None:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setMinimumWidth(420)
        label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        label.setMinimumHeight(max(28, label.sizeHint().height()))
        label.setObjectName("statusLabel" if ok is not False else "errorLabel")
        self.add_item(label)
        self._reset_stream_targets()

    def add_event_status(
        self, kind: str, text: str, ok: bool | None = None
    ) -> None:
        if kind == "verification":
            text = tr(self.language, "verification_status")
        elif kind == "stopped":
            text = tr(self.language, "stopped_status")
        elif kind == "persistence_warning":
            text = tr(self.language, "persistence_warning_status")
        elif kind == "persistence_recovered":
            text = tr(self.language, "persistence_recovered_status")
        self.add_status(text, ok)

    def render(self, transcript: list[dict]) -> None:
        self.clear_transcript()
        pending_calls: dict[str, dict] = {}
        for item in transcript:
            kind = item.get("type")
            if kind == "user":
                self.add_user(str(item.get("text", "")), list(item.get("attachments", [])))
            elif kind == "reasoning":
                self.append_reasoning(str(item.get("text", "")))
                self._reasoning = None
            elif kind in {"assistant", "assistant_stream"}:
                self.finish_assistant(str(item.get("text", "")))
            elif kind == "tool_call":
                call_id = item.get("call_id")
                self.add_tool(str(item.get("name", "tool")), str(item.get("text", "")), call_id)
                if call_id:
                    pending_calls[call_id] = item
            elif kind == "tool_result":
                self.finish_tool(
                    str(item.get("name", "tool")),
                    str(item.get("text", "")),
                    bool(item.get("ok")),
                    item.get("call_id"),
                )
            elif kind == "status":
                self.add_event_status(
                    str(item.get("kind", "")),
                    str(item.get("text", "")),
                    item.get("ok"),
                )
        self.layout.activate()
        self.container.adjustSize()
        self._scroll_bottom()

    def _reset_stream_targets(self) -> None:
        self._reasoning = None
        self._assistant = None

    def _scroll_bottom(self) -> None:
        item = self.layout.itemAt(self.layout.count() - 1)
        widget = item.widget() if item else None
        if widget is not None:
            def scroll(target: QWidget = widget) -> None:
                self.verticalScrollBar().setValue(
                    min(self.verticalScrollBar().maximum(), target.geometry().top())
                )

            QTimer.singleShot(0, lambda: QTimer.singleShot(0, scroll))
