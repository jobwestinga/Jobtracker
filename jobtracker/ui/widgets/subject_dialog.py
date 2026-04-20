"""
New / Edit subject dialog with color picker and validation feedback.
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QTextEdit, QPushButton, QColorDialog,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor


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


class SubjectDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New Subject")
        self.setFixedSize(380, 440)
        self.selected_color = PRESET_COLORS[0]
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
        c = QColorDialog.getColor(QColor(self.selected_color), self, "Choose Color")
        if c.isValid():
            self._select_color(c.name())

    def _refresh_swatches(self) -> None:
        for btn in self._swatch_btns:
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
