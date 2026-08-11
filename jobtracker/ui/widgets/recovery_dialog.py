"""
Active-session recovery dialog.

Thin UI over ``jobtracker.core.recovery``. Shown at startup when an unfinished
session is found with a meaningful gap since it was last known active. Lets the
user decide how much of the elapsed time was real work — the app never assumes
the whole gap was work, and never deletes the session automatically.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QDateTime, Qt
from PySide6.QtWidgets import (
    QButtonGroup, QDateTimeEdit, QHBoxLayout, QLabel,
    QPushButton, QRadioButton, QSpinBox, QVBoxLayout, QWidget,
)

from ...core import recovery
from ...core.recovery import RecoveryInfo
from .dialog_utils import InlineDialog, configure_window_modal


def _fmt_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, _s = divmod(rem, 60)
    if h and m:
        return f"{h}h {m}m"
    if h:
        return f"{h}h"
    return f"{m}m"


def _fmt_dt(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d  %H:%M:%S")


class RecoveryDialog(InlineDialog):
    def __init__(self, info: RecoveryInfo, parent=None) -> None:
        super().__init__(parent)
        configure_window_modal(self)
        self._info = info
        self.setWindowTitle("Resume Unfinished Session")
        self.setMinimumWidth(440)
        self._build_ui()

    def _build_ui(self) -> None:
        info = self._info
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(12)

        title = QLabel(f"“{info.subject_name}” was still being tracked")
        title.setStyleSheet("font-size: 15px; font-weight: 700;")
        title.setWordWrap(True)
        layout.addWidget(title)

        facts = QLabel(
            f"Started:           {_fmt_dt(info.start)}\n"
            f"Last known active: {_fmt_dt(info.last_active)}\n"
            f"Now:               {_fmt_dt(info.now)}\n"
            f"Unaccounted gap:   {_fmt_duration(info.gap_seconds)}"
        )
        facts.setStyleSheet("font-family: 'SF Mono','Menlo',monospace; font-size: 12px;")
        layout.addWidget(facts)

        hint = QLabel("How much of this should be saved as tracked time?")
        hint.setStyleSheet("font-weight: 600;")
        layout.addWidget(hint)

        self._group = QButtonGroup(self)

        # A. End at last known active time (recommended, default).
        self._opt_last = QRadioButton(
            f"End at last known active time  ·  {_fmt_duration(info.duration_if_last_seconds)}"
        )
        self._opt_last.setChecked(True)
        self._group.addButton(self._opt_last)
        layout.addWidget(self._opt_last)

        # B. End now (counts the whole gap as work).
        self._opt_now = QRadioButton(
            f"End now  ·  {_fmt_duration(info.duration_if_now_seconds)}"
        )
        self._group.addButton(self._opt_now)
        layout.addWidget(self._opt_now)

        # C. Custom end time.
        self._opt_custom_end = QRadioButton("End at a custom time:")
        self._group.addButton(self._opt_custom_end)
        layout.addWidget(self._opt_custom_end)

        self._end_edit = QDateTimeEdit(self)
        self._end_edit.setDisplayFormat("yyyy-MM-dd  HH:mm:ss")
        self._end_edit.setCalendarPopup(True)
        self._end_edit.setMinimumDateTime(QDateTime(info.start))
        self._end_edit.setMaximumDateTime(QDateTime(info.now))
        self._end_edit.setDateTime(QDateTime(info.last_active))
        self._end_edit.setEnabled(False)
        layout.addWidget(self._end_edit)

        # D. Custom length from start.
        self._opt_custom_len = QRadioButton("Set a session length:")
        self._group.addButton(self._opt_custom_len)
        layout.addWidget(self._opt_custom_len)

        len_row = QWidget()
        len_lay = QHBoxLayout(len_row)
        len_lay.setContentsMargins(0, 0, 0, 0)
        self._len_h = QSpinBox()
        self._len_h.setRange(0, 9999)
        self._len_h.setSuffix(" h")
        self._len_m = QSpinBox()
        self._len_m.setRange(0, 59)
        self._len_m.setSuffix(" m")
        default_len = info.duration_if_last_seconds
        self._len_h.setValue(default_len // 3600)
        self._len_m.setValue((default_len % 3600) // 60)
        self._len_h.setEnabled(False)
        self._len_m.setEnabled(False)
        len_lay.addWidget(self._len_h)
        len_lay.addWidget(self._len_m)
        len_lay.addStretch()
        layout.addWidget(len_row)

        self._opt_custom_end.toggled.connect(self._end_edit.setEnabled)
        self._opt_custom_len.toggled.connect(self._len_h.setEnabled)
        self._opt_custom_len.toggled.connect(self._len_m.setEnabled)

        # Buttons.
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        save = QPushButton("Save Session")
        save.setObjectName("primaryBtn")
        save.setMinimumHeight(36)
        save.setCursor(Qt.PointingHandCursor)
        save.clicked.connect(self.accept)
        btn_row.addWidget(save)
        layout.addLayout(btn_row)

    def chosen_end(self) -> datetime:
        """Resolve the selected option into an end datetime (delegates to core)."""
        if self._opt_now.isChecked():
            return recovery.end_time_for_choice(self._info, recovery.CHOICE_NOW)
        if self._opt_custom_end.isChecked():
            return recovery.end_time_for_choice(
                self._info, recovery.CHOICE_CUSTOM_END,
                custom_end=self._end_edit.dateTime().toPython(),
            )
        if self._opt_custom_len.isChecked():
            seconds = self._len_h.value() * 3600 + self._len_m.value() * 60
            return recovery.end_time_for_choice(
                self._info, recovery.CHOICE_CUSTOM_LENGTH, custom_length_seconds=seconds
            )
        return recovery.end_time_for_choice(self._info, recovery.CHOICE_LAST)
