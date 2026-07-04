"""
New / Edit subject dialog with color picker and validation feedback.
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QTextEdit, QPushButton, QColorDialog,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from ...core.colors import suggest_colors
from .dialog_utils import InlineDialog, configure_window_modal, open_dialog


# ── Preset colour palette ────────────────────────────────────────────────────
PRESET_COLORS = [
    "#3B82F6",  # Blue
    "#8B5CF6",  # Violet
    "#EC4899",  # Pink
    "#EF4444",  # Red
    "#F97316",  # Orange
    "#EAB308",  # Yellow
    "#22C55E",  # Green
    "#06B6D4",  # Cyan
]


class SubjectDialog(InlineDialog):
    def __init__(self, parent=None, existing_colors=None) -> None:
        super().__init__(parent)
        configure_window_modal(self)
        self.setWindowTitle("New Subject")
        self.setFixedSize(380, 500)
        self.selected_color = PRESET_COLORS[0]
        # Colours already used by ACTIVE subjects, so suggestions stay distinct.
        self._existing_colors = list(existing_colors or [])
        self._suggested_colors = suggest_colors(self._existing_colors, count=3)
        # Default a new subject to the first suggestion when we have existing
        # subjects to contrast against (keeps the palette varied automatically).
        if self._existing_colors and self._suggested_colors:
            self.selected_color = self._suggested_colors[0]
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(14)

        # ── Name ─────────────────────────────────────────────────────────
        lbl = QLabel("Subject Name")
        lbl.setStyleSheet("font-weight: 600;")
        layout.addWidget(lbl)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("e.g. Physics, Marketing, Music Theory …")
        self.name_input.setMinimumHeight(36)
        layout.addWidget(self.name_input)

        # ── Colour ───────────────────────────────────────────────────────
        color_lbl = QLabel("Accent Color")
        color_lbl.setStyleSheet("font-weight: 600;")
        layout.addWidget(color_lbl)

        # Preset swatches
        swatch_row = QHBoxLayout()
        swatch_row.setSpacing(6)
        self._swatch_btns: list[QPushButton] = []
        for c in PRESET_COLORS:
            btn = QPushButton()
            btn.setFixedSize(28, 28)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setProperty("hex", c)
            btn.clicked.connect(lambda checked, color=c: self._select_color(color))
            swatch_row.addWidget(btn)
            self._swatch_btns.append(btn)

        # Custom colour button
        custom_btn = QPushButton("...")
        custom_btn.setFixedSize(28, 28)
        custom_btn.setCursor(Qt.PointingHandCursor)
        custom_btn.setToolTip("Choose custom color")
        custom_btn.clicked.connect(self._pick_custom)
        swatch_row.addWidget(custom_btn)
        swatch_row.addStretch()
        layout.addLayout(swatch_row)

        # Preview
        preview_row = QHBoxLayout()
        self.color_preview = QLabel()
        self.color_preview.setFixedSize(20, 20)
        preview_row.addWidget(self.color_preview)
        self._color_hex_lbl = QLabel(self.selected_color)
        self._color_hex_lbl.setStyleSheet("font-size: 12px; opacity: 0.7;")
        preview_row.addWidget(self._color_hex_lbl)
        preview_row.addStretch()
        layout.addLayout(preview_row)

        # ── Suggested colours (distinct from active subjects) ─────────────
        if self._suggested_colors:
            suggest_lbl = QLabel("Suggested (distinct from your subjects)")
            suggest_lbl.setStyleSheet("font-size: 11px; opacity: 0.7;")
            layout.addWidget(suggest_lbl)

            suggest_row = QHBoxLayout()
            suggest_row.setSpacing(6)
            self._suggest_btns: list[QPushButton] = []
            for c in self._suggested_colors:
                btn = QPushButton()
                btn.setFixedSize(28, 28)
                btn.setCursor(Qt.PointingHandCursor)
                btn.setToolTip(f"Use {c}")
                btn.setProperty("hex", c)
                btn.clicked.connect(lambda checked, color=c: self._select_color(color))
                suggest_row.addWidget(btn)
                self._suggest_btns.append(btn)
            suggest_row.addStretch()
            layout.addLayout(suggest_row)

        self._refresh_swatches()

        # ── Notes ────────────────────────────────────────────────────────
        notes_lbl = QLabel("Notes")
        notes_lbl.setStyleSheet("font-weight: 600;")
        layout.addWidget(notes_lbl)

        self.notes_input = QTextEdit()
        self.notes_input.setPlaceholderText("Optional — add context or details …")
        self.notes_input.setMaximumHeight(80)
        layout.addWidget(self.notes_input)

        layout.addStretch()

        # ── Actions ──────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setMinimumHeight(36)
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        save_btn = QPushButton("Save Subject")
        save_btn.setObjectName("primaryBtn")
        save_btn.setMinimumHeight(36)
        save_btn.setCursor(Qt.PointingHandCursor)
        save_btn.clicked.connect(self.accept)
        btn_row.addWidget(save_btn)

        layout.addLayout(btn_row)

        for b in self.findChildren(QPushButton):
            b.setAutoDefault(False)
            b.setDefault(False)
        save_btn.setAutoDefault(True)
        save_btn.setDefault(True)

    # ── Colour helpers ───────────────────────────────────────────────────
    def _select_color(self, hex_color: str) -> None:
        self.selected_color = hex_color
        self._refresh_swatches()

    def _pick_custom(self) -> None:
        dialog = QColorDialog(QColor(self.selected_color), self)
        dialog.setWindowTitle("Choose Color")
        # The native QColorPanel is a separate floating macOS window. Use the
        # Qt dialog so it stays attached in a native-fullscreen Space.
        dialog.setOption(QColorDialog.DontUseNativeDialog, True)
        configure_window_modal(dialog)
        open_dialog(dialog, self._finish_custom_color)

    def _finish_custom_color(
        self, result: int, dialog: QColorDialog
    ) -> None:
        if result != QDialog.Accepted:
            return
        color = dialog.selectedColor()
        if color.isValid():
            self._select_color(color.name())

    def _refresh_swatches(self) -> None:
        for btn in self._swatch_btns + getattr(self, "_suggest_btns", []):
            c = btn.property("hex")
            ring = "border: 2px solid white;" if c == self.selected_color else "border: 2px solid transparent;"
            btn.setStyleSheet(
                f"background-color: {c}; border-radius: 14px; {ring}"
            )
        self.color_preview.setStyleSheet(
            f"background-color: {self.selected_color}; border-radius: 10px;"
        )
        self._color_hex_lbl.setText(self.selected_color)

    # ── Data ─────────────────────────────────────────────────────────────
    def get_data(self) -> dict:
        return {
            "name": self.name_input.text().strip(),
            "color": self.selected_color,
            "notes": self.notes_input.toPlainText().strip(),
        }
