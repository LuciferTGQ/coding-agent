"""Compact painted toggle used for per-session capabilities."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QEnterEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import QAbstractButton, QWidget


class ToggleSwitch(QAbstractButton):
    """A small accessible on/off switch without an external style dependency."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(self.sizeHint())
        self._hovered = False
        self.toggled.connect(lambda _: self.update())

    def sizeHint(self) -> QSize:
        return QSize(40, 22)

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        if not self.isEnabled():
            track = QColor("#2b2f38")
            knob = QColor("#666d7a")
        elif self.isChecked():
            track = QColor("#4b76dc" if not self._hovered else "#5a84ea")
            knob = QColor("#f7f9fc")
        else:
            track = QColor("#3a404c" if not self._hovered else "#474e5c")
            knob = QColor("#c7ced9")
        painter.setBrush(track)
        painter.drawRoundedRect(QRectF(0, 1, self.width(), self.height() - 2), 10, 10)
        diameter = self.height() - 6
        x = self.width() - diameter - 3 if self.isChecked() else 3
        painter.setBrush(knob)
        painter.drawEllipse(QRectF(x, 3, diameter, diameter))

    def enterEvent(self, event: QEnterEvent) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.update()
        super().leaveEvent(event)
