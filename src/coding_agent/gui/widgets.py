"""Small transcript widgets with collapsible reasoning and tool details."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class MessageBubble(QFrame):
    def __init__(self, role: str, text: str = "") -> None:
        super().__init__()
        self.setObjectName("userBubble" if role == "user" else "assistantBubble")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        heading = QLabel("You" if role == "user" else "Agent")
        heading.setObjectName("bubbleHeading")
        self.body = QLabel(text)
        self.body.setWordWrap(True)
        self.body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.body.setTextFormat(Qt.PlainText)
        layout.addWidget(heading)
        layout.addWidget(self.body)
        self.setMaximumWidth(860)
        if role != "user":
            self.setMinimumWidth(520)

    def append_text(self, delta: str) -> None:
        self.body.setText(self.body.text() + delta)

    def set_text(self, text: str) -> None:
        self.body.setText(text)


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
    def __init__(self, name: str, summary: str, call_id: str | None = None) -> None:
        super().__init__(f"●  {name}  ·  running", summary)
        self.name = name
        self.call_id = call_id

    def set_result(self, text: str, ok: bool) -> None:
        status = "completed" if ok else "failed"
        marker = "✓" if ok else "×"
        self.toggle.setText(f"{marker}  {self.name}  ·  {status}")
        existing = self.detail.toPlainText()
        self.detail.setPlainText((existing + "\n\n" if existing else "") + text)
        self.setProperty("failed", not ok)
        self.style().unpolish(self)
        self.style().polish(self)


class ConversationView(QScrollArea):
    def __init__(self) -> None:
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.container = QWidget()
        self.layout = QVBoxLayout(self.container)
        self.layout.setContentsMargins(24, 24, 24, 24)
        self.layout.setSpacing(12)
        self.layout.addStretch(1)
        self.setWidget(self.container)
        self._reasoning: CollapsibleCard | None = None
        self._assistant: MessageBubble | None = None
        self._tools: dict[str, ToolCard] = {}

    def clear_transcript(self) -> None:
        while self.layout.count() > 1:
            item = self.layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._reasoning = None
        self._assistant = None
        self._tools = {}

    def add_item(self, widget: QWidget, *, align_right: bool = False) -> None:
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        if align_right:
            row_layout.addStretch(1)
            row_layout.addWidget(widget)
        else:
            row_layout.addWidget(widget)
            row_layout.addStretch(1)
        self.layout.insertWidget(self.layout.count() - 1, row)
        self._scroll_bottom()

    def add_user(self, text: str, attachments: list[str] | None = None) -> None:
        rendered = text
        if attachments:
            rendered += "\n\nFiles: " + ", ".join(attachments)
        self.add_item(MessageBubble("user", rendered), align_right=True)
        self._reset_stream_targets()

    def append_reasoning(self, delta: str) -> None:
        if self._reasoning is None:
            self._reasoning = CollapsibleCard("Reasoning", "")
            self.add_item(self._reasoning)
        self._reasoning.append_text(delta)

    def append_assistant(self, delta: str) -> None:
        if self._assistant is None:
            self._assistant = MessageBubble("assistant", "")
            self.add_item(self._assistant)
        self._assistant.append_text(delta)

    def finish_assistant(self, text: str) -> None:
        if self._assistant is None:
            self._assistant = MessageBubble("assistant", text)
            self.add_item(self._assistant)
        else:
            self._assistant.set_text(text)
        self._reasoning = None
        self._assistant = None

    def add_tool(self, name: str, summary: str, call_id: str | None) -> None:
        card = ToolCard(name, summary, call_id)
        self.add_item(card)
        if call_id:
            self._tools[call_id] = card
        self._reasoning = None
        self._assistant = None

    def finish_tool(self, name: str, text: str, ok: bool, call_id: str | None) -> None:
        card = self._tools.get(call_id or "")
        if card is None:
            card = ToolCard(name, "", call_id)
            self.add_item(card)
        card.set_result(text, ok)

    def add_status(self, text: str, ok: bool | None = None) -> None:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setObjectName("statusLabel" if ok is not False else "errorLabel")
        self.add_item(label)
        self._reset_stream_targets()

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
                self.add_status(str(item.get("text", "")), item.get("ok"))

    def _reset_stream_targets(self) -> None:
        self._reasoning = None
        self._assistant = None

    def _scroll_bottom(self) -> None:
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())
