"""
Shared label helpers.

``ElidedLabel`` is the one card labels should use for any text that can be long
(titles, descriptions). A plain non-wrapping QLabel reports its full text width
as its *minimum* size hint, which makes the whole card refuse to shrink and
overflow its row in a narrow window. This one shrinks and ends in an ellipsis.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QLabel, QSizePolicy


class ElidedLabel(QLabel):
    """One-line label that truncates with an ellipsis instead of wrapping."""

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(parent)
        self._full_text = text or ""
        self.setWordWrap(False)
        # Expanding so the label absorbs the row's spare width. Without a truly
        # expanding child Qt spreads the slack evenly between every item, which
        # pushes leading controls into the middle of the card.
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._apply_elide()

    def full_text(self) -> str:
        return self._full_text

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        # Never force the card wider than its row: the text elides instead.
        return QSize(40, super().minimumSizeHint().height())

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(200, super().sizeHint().height())

    def setText(self, text: str) -> None:  # noqa: N802
        self._full_text = text or ""
        self._apply_elide()

    def _apply_elide(self) -> None:
        metrics = QFontMetrics(self.font())
        super().setText(
            metrics.elidedText(self._full_text, Qt.ElideRight, max(40, self.width()))
        )

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply_elide()
