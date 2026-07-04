"""
Shared helpers for the custom-painted graph widgets (bar chart, agenda,
heatmap, FX background): alpha-adjusted colours and the floating hover card.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QLabel, QWidget


def with_alpha(hex_color: str, alpha: int) -> QColor:
    color = QColor(hex_color)
    color.setAlpha(max(0, min(255, alpha)))
    return color


class HoverCard(QLabel):
    """Floating rich-text tooltip that follows the cursor inside a canvas.

    Kept inside the owning widget (no native tooltip window) and clamped to the
    visible region so it works inside horizontal scroll areas.
    """

    def __init__(self, owner: QWidget) -> None:
        super().__init__(owner)
        self._owner = owner
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setTextFormat(Qt.RichText)
        self.setContentsMargins(10, 7, 10, 7)
        self.hide()

    def apply_tokens(self, tokens: dict) -> None:
        background = tokens.get("BG_SECONDARY", "#131D2E")
        text = tokens.get("TEXT_PRIMARY", "#E2E8F0")
        border = tokens.get("BORDER_FOCUS", tokens.get("ACCENT", "#3B82F6"))
        self.setStyleSheet(
            "QLabel { "
            f"background-color: {background}; color: {text}; "
            f"border: 1px solid {border}; "
            "border-radius: 8px; font-size: 11px; }"
        )

    def show_at(self, x: float, y: float, html: str) -> None:
        """Show next to cursor position (owner coordinates), kept in view."""
        self.setText(html)
        self.adjustSize()
        visible = self._owner.visibleRegion().boundingRect()
        if visible.isEmpty():
            visible = self._owner.rect()
        left = int(x) + 14
        top = int(y) - self.height() // 2
        if left + self.width() > visible.right():
            left = int(x) - self.width() - 14
        left = max(visible.left() + 4, min(left, visible.right() - self.width() - 4))
        top = max(visible.top() + 4, min(top, visible.bottom() - self.height() - 4))
        self.move(left, top)
        self.show()
        self.raise_()
