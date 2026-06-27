"""
Graph Settings dialog — configures the Graphs tab:
- Time range (7 / 14 / 30 / All Time) or a one-off custom from/to range
- Grouping (Daily / Weekly / Monthly) for the stacked bar chart
- View mode (Stacked Bar / Agenda Timeline)
- Agenda visible hour range (start / end hour) + auto-fit
"""

from datetime import date

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QSpinBox, QCheckBox, QDateEdit,
)
from PySide6.QtCore import Qt, QDate

from ...core.database import db


RANGE_OPTIONS = [
    ("7 days", 7),
    ("14 days", 14),
    ("30 days", 30),
    ("All Time", None),
]

GROUPING_OPTIONS = [
    ("Daily", "daily"),
    ("Weekly", "weekly"),
    ("Monthly", "monthly"),
]

VIEW_OPTIONS = [
    ("Stacked Bar", "bar"),
    ("Agenda Timeline", "agenda"),
]


class GraphSettingsDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Graph Settings")
        self.setFixedSize(440, 620)
        self._selected_range = 0
        self._selected_grouping = 0
        self._selected_mode = 0
        self._build_ui()
        self._load_current()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(12)

        # ── Time Range ───────────────────────────────────────────────────
        lbl_range = QLabel("Time Range")
        lbl_range.setStyleSheet("font-weight: 600; font-size: 13px;")
        layout.addWidget(lbl_range)

        self.range_btns = []
        range_row = QHBoxLayout()
        range_row.setSpacing(6)
        for idx, (label, _) in enumerate(RANGE_OPTIONS):
            btn = QPushButton(label)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setMinimumHeight(32)
            btn.setStyleSheet("padding: 0 4px;")
            btn.setSizePolicy(btn.sizePolicy().Policy.MinimumExpanding, btn.sizePolicy().Policy.Fixed)
            btn.clicked.connect(lambda checked, i=idx: self._select_range(i))
            range_row.addWidget(btn)
            self.range_btns.append(btn)
        layout.addLayout(range_row)

        # ── Custom range ─────────────────────────────────────────────────
        self.custom_check = QCheckBox("Use a custom date range")
        self.custom_check.setCursor(Qt.PointingHandCursor)
        self.custom_check.toggled.connect(self._on_custom_toggled)
        layout.addWidget(self.custom_check)

        custom_row = QHBoxLayout()
        custom_row.setSpacing(8)
        custom_row.addWidget(QLabel("From:"))
        self.from_date = QDateEdit()
        self.from_date.setCalendarPopup(True)
        self.from_date.setDisplayFormat("yyyy-MM-dd")
        self.from_date.setDate(QDate.currentDate().addDays(-29))
        custom_row.addWidget(self.from_date)
        custom_row.addWidget(QLabel("To:"))
        self.to_date = QDateEdit()
        self.to_date.setCalendarPopup(True)
        self.to_date.setDisplayFormat("yyyy-MM-dd")
        self.to_date.setDate(QDate.currentDate())
        custom_row.addWidget(self.to_date)
        self._custom_row = custom_row
        layout.addLayout(custom_row)

        # ── Grouping ─────────────────────────────────────────────────────
        lbl_group = QLabel("Grouping (Bar chart)")
        lbl_group.setStyleSheet("font-weight: 600; font-size: 13px;")
        layout.addWidget(lbl_group)

        self.group_btns = []
        group_row = QHBoxLayout()
        group_row.setSpacing(6)
        for idx, (label, _) in enumerate(GROUPING_OPTIONS):
            btn = QPushButton(label)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setMinimumHeight(32)
            btn.setSizePolicy(btn.sizePolicy().Policy.MinimumExpanding, btn.sizePolicy().Policy.Fixed)
            btn.clicked.connect(lambda checked, i=idx: self._select_grouping(i))
            group_row.addWidget(btn)
            self.group_btns.append(btn)
        layout.addLayout(group_row)

        # ── View Mode ────────────────────────────────────────────────────
        lbl_mode = QLabel("View Mode")
        lbl_mode.setStyleSheet("font-weight: 600; font-size: 13px;")
        layout.addWidget(lbl_mode)

        self.mode_btns = []
        mode_row = QHBoxLayout()
        mode_row.setSpacing(6)
        for idx, (label, _) in enumerate(VIEW_OPTIONS):
            btn = QPushButton(label)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setMinimumHeight(32)
            btn.setSizePolicy(btn.sizePolicy().Policy.MinimumExpanding, btn.sizePolicy().Policy.Fixed)
            btn.clicked.connect(lambda checked, i=idx: self._select_mode(i))
            mode_row.addWidget(btn)
            self.mode_btns.append(btn)
        layout.addLayout(mode_row)

        # ── Fit Width Toggle ─────────────────────────────────────────────
        self.fit_width_check = QCheckBox("Fit Width to Screen (No horizontal scrolling)")
        self.fit_width_check.setCursor(Qt.PointingHandCursor)
        layout.addWidget(self.fit_width_check)

        # ── Agenda Hours ─────────────────────────────────────────────────
        self.autofit_hours_check = QCheckBox("Auto-fit time bounds to actual work")
        self.autofit_hours_check.setCursor(Qt.PointingHandCursor)
        self.autofit_hours_check.toggled.connect(self._on_autofit_toggled)
        layout.addWidget(self.autofit_hours_check)

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

        for b in self.findChildren(QPushButton):
            b.setAutoDefault(False)
            b.setDefault(False)
        save.setAutoDefault(True)
        save.setDefault(True)

    # ── selection helpers ────────────────────────────────────────────────
    def _restyle(self, buttons, selected_idx):
        for i, btn in enumerate(buttons):
            btn.setObjectName("primaryBtn" if i == selected_idx else "")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _select_range(self, idx: int) -> None:
        self._selected_range = idx
        # Choosing a preset turns off the custom range.
        if self.custom_check.isChecked():
            self.custom_check.setChecked(False)
        self._restyle(self.range_btns, idx)

    def _select_grouping(self, idx: int) -> None:
        self._selected_grouping = idx
        self._restyle(self.group_btns, idx)

    def _select_mode(self, idx: int) -> None:
        self._selected_mode = idx
        self._restyle(self.mode_btns, idx)
        self._on_mode_changed()

    def _on_custom_toggled(self, checked: bool) -> None:
        for i in range(self._custom_row.count()):
            w = self._custom_row.itemAt(i).widget()
            if w:
                w.setEnabled(checked)
        if checked:
            # Visually clear preset selection while custom is active.
            self._restyle(self.range_btns, -1)
        else:
            self._restyle(self.range_btns, self._selected_range)

    def _on_mode_changed(self) -> None:
        is_agenda = self._selected_mode == 1
        self.autofit_hours_check.setVisible(is_agenda)
        self._on_autofit_toggled(self.autofit_hours_check.isChecked())

    def _on_autofit_toggled(self, checked: bool) -> None:
        is_agenda = self._selected_mode == 1
        show_hours = is_agenda and not checked
        self._hours_label.setVisible(show_hours)
        for i in range(self._hours_row.count()):
            widget = self._hours_row.itemAt(i).widget()
            if widget:
                widget.setVisible(show_hours)

    # ── load / save ──────────────────────────────────────────────────────
    def _load_current(self) -> None:
        saved_range = db.get_setting("graph_range", "7")
        is_custom = saved_range == "custom"

        idx_to_select = 0
        for idx, (_, value) in enumerate(RANGE_OPTIONS):
            str_val = str(value) if value is not None else "all"
            if saved_range == str_val:
                idx_to_select = idx
                break
        self._select_range(idx_to_select)

        # Grouping
        saved_group = db.get_setting("graph_grouping", "daily")
        group_idx = 0
        for idx, (_, value) in enumerate(GROUPING_OPTIONS):
            if saved_group == value:
                group_idx = idx
                break
        self._select_grouping(group_idx)

        # View mode
        saved_mode = db.get_setting("graph_view_mode", "bar")
        mode_idx = 0
        for idx, (_, value) in enumerate(VIEW_OPTIONS):
            if saved_mode == value:
                mode_idx = idx
                break
        self._select_mode(mode_idx)

        # Custom dates
        start_raw = db.get_setting("graph_custom_start", "")
        end_raw = db.get_setting("graph_custom_end", "")
        if start_raw:
            try:
                d = date.fromisoformat(start_raw)
                self.from_date.setDate(QDate(d.year, d.month, d.day))
            except ValueError:
                pass
        if end_raw:
            try:
                d = date.fromisoformat(end_raw)
                self.to_date.setDate(QDate(d.year, d.month, d.day))
            except ValueError:
                pass
        self.custom_check.setChecked(is_custom)
        self._on_custom_toggled(is_custom)

        self.fit_width_check.setChecked(db.get_setting("graph_fit_horizontal", "1") == "1")
        self.autofit_hours_check.setChecked(db.get_setting("graph_autofit_hours", "0") == "1")

        try:
            self.hour_start_spin.setValue(int(db.get_setting("graph_hour_start", "6")))
        except ValueError:
            pass
        try:
            self.hour_end_spin.setValue(int(db.get_setting("graph_hour_end", "23")))
        except ValueError:
            pass

        self._on_autofit_toggled(self.autofit_hours_check.isChecked())

    def _save_and_accept(self) -> None:
        if self.hour_start_spin.value() >= self.hour_end_spin.value():
            self.hour_end_spin.setValue(self.hour_start_spin.value() + 1)

        s = self.get_settings()
        db.set_setting("graph_range", s["range_str"])
        db.set_setting("graph_grouping", s["grouping"])
        db.set_setting("graph_view_mode", s["view_mode"])
        db.set_setting("graph_hour_start", str(s["hour_start"]))
        db.set_setting("graph_hour_end", str(s["hour_end"]))
        db.set_setting("graph_fit_horizontal", "1" if s["fit_horizontal"] else "0")
        db.set_setting("graph_autofit_hours", "1" if s["autofit_hours"] else "0")
        if s["custom_range"] is not None:
            db.set_setting("graph_custom_start", s["custom_range"][0].isoformat())
            db.set_setting("graph_custom_end", s["custom_range"][1].isoformat())
        self.accept()

    def get_settings(self) -> dict:
        _, range_val = RANGE_OPTIONS[self._selected_range]
        _, grouping_val = GROUPING_OPTIONS[self._selected_grouping]
        _, mode_val = VIEW_OPTIONS[self._selected_mode]

        custom_range = None
        range_str = str(range_val) if range_val is not None else "all"
        if self.custom_check.isChecked():
            start_q = self.from_date.date()
            end_q = self.to_date.date()
            start = date(start_q.year(), start_q.month(), start_q.day())
            end = date(end_q.year(), end_q.month(), end_q.day())
            if end < start:
                start, end = end, start
            custom_range = (start, end)
            range_str = "custom"
            range_val = None

        return {
            "range_days": range_val,
            "range_str": range_str,
            "custom_range": custom_range,
            "grouping": grouping_val,
            "view_mode": mode_val,
            "hour_start": self.hour_start_spin.value(),
            "hour_end": self.hour_end_spin.value(),
            "fit_horizontal": self.fit_width_check.isChecked(),
            "autofit_hours": self.autofit_hours_check.isChecked(),
        }
