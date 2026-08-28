"""Compact session rows used by the desktop conversation sidebar."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QContextMenuEvent, QEnterEvent, QMouseEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from coding_agent.gui.i18n import tr
from coding_agent.sessions import Session


class SessionRow(QWidget):
    """A selectable session summary with a hover-only actions button."""

    clicked = Signal()
    menu_requested = Signal(object)

    def __init__(self, session: Session, language: str) -> None:
        super().__init__()
        self.session_id = session.id
        self.language = language
        self._hovered = False
        self._selected = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(9, 7, 5, 7)
        layout.setSpacing(7)
        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)
        self.title_label = QLabel()
        self.title_label.setObjectName("sessionTitle")
        self.title_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.workspace_label = QLabel()
        self.workspace_label.setObjectName("sessionWorkspace")
        self.workspace_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.marker_label = QLabel()
        self.marker_label.setObjectName("sessionMarker")
        self.marker_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.marker_label.setAlignment(Qt.AlignCenter)
        self.marker_label.setMinimumWidth(28)
        self.menu_button = QToolButton()
        self.menu_button.setObjectName("sessionMenuButton")
        self.menu_button.setText("...")
        self.menu_button.setAutoRaise(True)
        self.menu_button.setToolTip(tr(language, "session_actions"))
        self.menu_button.clicked.connect(
            lambda: self.menu_requested.emit(
                self.menu_button.mapToGlobal(self.menu_button.rect().bottomLeft())
            )
        )
        menu_slot = QWidget()
        menu_slot.setFixedWidth(28)
        menu_layout = QHBoxLayout(menu_slot)
        menu_layout.setContentsMargins(0, 0, 0, 0)
        menu_layout.addWidget(self.menu_button)
        text_layout.addWidget(self.title_label)
        text_layout.addWidget(self.workspace_label)
        layout.addLayout(text_layout, 1)
        layout.addWidget(self.marker_label)
        layout.addWidget(menu_slot)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.set_session(session)
        self._sync_menu_visibility()

    def set_session(self, session: Session, *, running_text: str = "") -> None:
        self.title_label.setText(session.title)
        self.title_label.setToolTip(session.title)
        self.workspace_label.setText(session.workspace.rsplit("\\", 1)[-1].rsplit("/", 1)[-1])
        self.workspace_label.setToolTip(session.workspace)
        markers: list[str] = []
        if session.pinned:
            markers.append("↑")
        if session.unread:
            markers.append("●")
        self.marker_label.setText(running_text or "  ".join(markers))
        self.marker_label.setToolTip(
            tr(self.language, "running")
            if running_text
            else tr(self.language, "session_markers")
        )

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self.setProperty("selected", selected)
        self.style().unpolish(self)
        self.style().polish(self)
        self._sync_menu_visibility()

    def enterEvent(self, event: QEnterEvent) -> None:
        self._hovered = True
        self._sync_style()
        self._sync_menu_visibility()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self._sync_style()
        self._sync_menu_visibility()
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        elif event.button() == Qt.RightButton:
            self.menu_requested.emit(event.globalPosition().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        # Mouse-triggered menus are handled in mousePressEvent so they also work
        # reliably for QListWidget item widgets. Accept the synthesized event to
        # avoid opening the same menu twice.
        event.accept()

    def _sync_menu_visibility(self) -> None:
        active = self._hovered or self._selected
        self.menu_button.setVisible(True)
        self.menu_button.setEnabled(active)
        self.menu_button.setText("..." if active else "")

    def _sync_style(self) -> None:
        self.setProperty("hovered", self._hovered)
        self.style().unpolish(self)
        self.style().polish(self)
