"""
Graph Settings dialog — lets the user configure the Graphs tab:
- Time range (7 / 14 / 30 / All Time)
- View mode (Stacked Bar / Agenda Timeline)
- Agenda visible hour range (start / end hour)
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QSpinBox, QCheckBox,
)
from PySide6.QtCore import Qt

from ...core.database import db


RANGE_OPTIONS = [
    ("7 days", 7),
    ("14 days", 14),
    ("30 days", 30),
    ("All Time", None),
]

VIEW_OPTIONS = [
    ("Stacked Bar", "bar"),
    ("Agenda Timeline", "agenda"),
]


class GraphSettingsDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Graph Settings")
        self.setFixedSize(360, 420)
        self._build_ui()
        self._load_current()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(14)

        # ── Time Range ───────────────────────────────────────────────────
        lbl_range = QLabel("Time Range")
        lbl_range.setStyleSheet("font-weight: 600; font-size: 13px;")
        layout.addWidget(lbl_range)

        self.range_combo = QComboBox()
        self.range_combo.setCursor(Qt.PointingHandCursor)
        for label, _ in RANGE_OPTIONS:
            self.range_combo.addItem(label)
        self.range_combo.setMinimumHeight(34)
        layout.addWidget(self.range_combo)

        # ── View Mode ────────────────────────────────────────────────────
        lbl_mode = QLabel("View Mode")
        lbl_mode.setStyleSheet("font-weight: 600; font-size: 13px;")
        layout.addWidget(lbl_mode)

        self.mode_combo = QComboBox()
        self.mode_combo.setCursor(Qt.PointingHandCursor)
        for label, _ in VIEW_OPTIONS:
            self.mode_combo.addItem(label)
        self.mode_combo.setMinimumHeight(34)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        layout.addWidget(self.mode_combo)

        # ── Fit Width Toggle ─────────────────────────────────────────────
        self.fit_width_check = QCheckBox("Fit Width to Screen (No horizontal scrolling)")
        self.fit_width_check.setCursor(Qt.PointingHandCursor)
        layout.addWidget(self.fit_width_check)

        layout.addSpacing(6)

        # ── Agenda Hours ─────────────────────────────────────────────────
        self._hours_label = QLabel("Visible Hour Range (Agenda)")
        self._hours_label.setStyleSheet("font-weight: 600; font-size: 13px;")
        layout.addWidget(self._hours_label)

        hours_row = QHBoxLayout()
        hours_row.setSpacing(10)

        lbl_from = QLabel("From:")
        lbl_from.setStyleSheet("font-size: 12px;")
        hours_row.addWidget(lbl_from)

        self.hour_start_spin = QSpinBox()
        self.hour_start_spin.setRange(0, 23)
        self.hour_start_spin.setValue(6)
        self.hour_start_spin.setSuffix(":00")
        self.hour_start_spin.setMinimumHeight(32)
        hours_row.addWidget(self.hour_start_spin)

        lbl_to = QLabel("To:")
        lbl_to.setStyleSheet("font-size: 12px;")
        hours_row.addWidget(lbl_to)

        self.hour_end_spin = QSpinBox()
        self.hour_end_spin.setRange(1, 24)
        self.hour_end_spin.setValue(23)
        self.hour_end_spin.setSuffix(":00")
        self.hour_end_spin.setMinimumHeight(32)
        hours_row.addWidget(self.hour_end_spin)

        self._hours_row = hours_row
        layout.addLayout(hours_row)

        self.autofit_hours_check = QCheckBox("Auto-fit time bounds to actual work")
        self.autofit_hours_check.setCursor(Qt.PointingHandCursor)
        self.autofit_hours_check.toggled.connect(self._on_autofit_toggled)
        layout.addWidget(self.autofit_hours_check)

        layout.addStretch()

        # ── Buttons ──────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        cancel = QPushButton("Cancel")
        cancel.setMinimumHeight(36)
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(cancel)

        save = QPushButton("Apply")
        save.setObjectName("primaryBtn")
        save.setMinimumHeight(36)
        save.setCursor(Qt.PointingHandCursor)
        save.clicked.connect(self._save_and_accept)
        btn_row.addWidget(save)

        layout.addLayout(btn_row)

        self._on_mode_changed()

    def _on_mode_changed(self) -> None:
        is_agenda = self.mode_combo.currentIndex() == 1
        self._hours_label.setVisible(is_agenda)
        self.hour_start_spin.setVisible(is_agenda)
        self.hour_end_spin.setVisible(is_agenda)
        self.autofit_hours_check.setVisible(is_agenda)
        # Also hide the labels in the hours row
        for i in range(self._hours_row.count()):
            widget = self._hours_row.itemAt(i).widget()
            if widget:
                widget.setVisible(is_agenda)

    def _on_autofit_toggled(self, checked: bool) -> None:
        self.hour_start_spin.setEnabled(not checked)
        self.hour_end_spin.setEnabled(not checked)

    def _load_current(self) -> None:
        # Time range
        saved_range = db.get_setting("graph_range", "7")
        for idx, (_, value) in enumerate(RANGE_OPTIONS):
            str_val = str(value) if value is not None else "all"
            if saved_range == str_val:
                self.range_combo.setCurrentIndex(idx)
                break

        # View mode
        saved_mode = db.get_setting("graph_view_mode", "bar")
        for idx, (_, value) in enumerate(VIEW_OPTIONS):
            if saved_mode == value:
                self.mode_combo.setCurrentIndex(idx)
                break

        # Fit options
        saved_fit = db.get_setting("graph_fit_horizontal", "1")
        self.fit_width_check.setChecked(saved_fit == "1")

        saved_auto = db.get_setting("graph_autofit_hours", "0")
        self.autofit_hours_check.setChecked(saved_auto == "1")

        # Hour range
        try:
            self.hour_start_spin.setValue(int(db.get_setting("graph_hour_start", "6")))
        except ValueError:
            pass
        try:
            self.hour_end_spin.setValue(int(db.get_setting("graph_hour_end", "23")))
        except ValueError:
            pass

        self._on_autofit_toggled(self.autofit_hours_check.isChecked())
        self._on_mode_changed()

    def _save_and_accept(self) -> None:
        # Ensure hour start < hour end
        if self.hour_start_spin.value() >= self.hour_end_spin.value():
            self.hour_end_spin.setValue(self.hour_start_spin.value() + 1)

        settings = self.get_settings()
        db.set_setting("graph_range", settings["range_str"])
        db.set_setting("graph_view_mode", settings["view_mode"])
        db.set_setting("graph_hour_start", str(settings["hour_start"]))
        db.set_setting("graph_hour_end", str(settings["hour_end"]))
        db.set_setting("graph_fit_horizontal", "1" if settings["fit_horizontal"] else "0")
        db.set_setting("graph_autofit_hours", "1" if settings["autofit_hours"] else "0")
        self.accept()

    def get_settings(self) -> dict:
        range_idx = self.range_combo.currentIndex()
        _, range_val = RANGE_OPTIONS[range_idx]
        range_str = str(range_val) if range_val is not None else "all"

        mode_idx = self.mode_combo.currentIndex()
        _, mode_val = VIEW_OPTIONS[mode_idx]

        return {
            "range_days": range_val,
            "range_str": range_str,
            "view_mode": mode_val,
            "hour_start": self.hour_start_spin.value(),
            "hour_end": self.hour_end_spin.value(),
            "fit_horizontal": self.fit_width_check.isChecked(),
            "autofit_hours": self.autofit_hours_check.isChecked(),
        }
