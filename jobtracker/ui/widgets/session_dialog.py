"""
Add / Edit session dialog with sensible datetime defaults
and start < end validation.

When editing an existing session (and a service is supplied), a Subject
dropdown lets the session be moved to another subject: same times and duration,
only the owning subject changes. The Move buttons nudge both ends together.

Sessions have no note field — it was removed from the schema.
"""

from PySide6.QtWidgets import (
    QComboBox, QVBoxLayout, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QDateTimeEdit, QSpinBox,
)
from PySide6.QtCore import QDateTime, QTime, QTimeZone, Qt

from ...core import timeutils

from .dialog_utils import (
    InlineDialog,
    configure_window_modal,
    question,
    warning,
)
from .session_list import build_move_row, resolve_tokens


def apply_session_edits(service, session, data: dict):
    """Persist a :class:`SessionDialog` result onto an existing session.

    Falls back to the session's current subject when the dialog had no subject
    picker, so an edit can never strand a session on a missing subject.
    """
    subject_id = data.get("subject_id") or session.subject_id
    return service.update_session(
        session.id,
        subject_id,
        data["start_time"],
        data["end_time"],
    )


class DurationDialog(InlineDialog):
    """Combine duration chunks, with an exact hours/minutes fallback."""

    MAX_HOURS = 99
    DURATION_CHUNKS = (
        ("4h", 240), ("2h", 120), ("1h", 60),
        ("30m", 30), ("15m", 15), ("10m", 10), ("5m", 5),
    )

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        configure_window_modal(self)
        self.setWindowTitle("Custom Duration")
        self.setMinimumSize(380, 360)
        self.resize(400, 380)
        self._syncing_duration = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(10)

        title = QLabel("How long was the session?")
        title.setStyleSheet("font-weight: 600;")
        layout.addWidget(title)

        tokens = resolve_tokens(self)
        self.chunk_buttons: dict[int, QPushButton] = {}
        for chunks in (self.DURATION_CHUNKS[:3], self.DURATION_CHUNKS[3:]):
            row = QHBoxLayout()
            row.setSpacing(8)
            for label, minutes in chunks:
                button = QPushButton(label, self)
                button.setCheckable(True)
                button.setMinimumHeight(40)
                button.setCursor(Qt.PointingHandCursor)
                button.setProperty("no_drag", True)
                button.setToolTip(f"Toggle {minutes} minutes in the total")
                button.setAccessibleName(f"Include {minutes} minutes in the session")
                button.setStyleSheet(
                    "QPushButton:checked {"
                    f" background: {tokens['BG_TERTIARY']};"
                    f" border: 2px solid {tokens['ACCENT']}; font-weight: 700; }}"
                )
                button.toggled.connect(self._chunks_changed)
                self.chunk_buttons[minutes] = button
                row.addWidget(button, 1)
            layout.addLayout(row)

        self.total_label = QLabel(self)
        self.total_label.setStyleSheet("font-size: 14px; font-weight: 600;")
        layout.addWidget(self.total_label)

        fields = QHBoxLayout()
        fields.setSpacing(8)
        fields.addWidget(QLabel("Exact duration", self))

        self.hours_input = QSpinBox(self)
        self.hours_input.setRange(0, self.MAX_HOURS)
        self.hours_input.setSuffix(" h")
        self.hours_input.setAccessibleName("Exact session duration hours")
        self.hours_input.setMinimumHeight(32)
        self.hours_input.setAlignment(Qt.AlignCenter)
        fields.addWidget(self.hours_input)

        self.minutes_input = QSpinBox(self)
        self.minutes_input.setRange(0, 59)  # minutes never reach a full hour
        self.minutes_input.setSuffix(" m")
        self.minutes_input.setAccessibleName("Exact session duration minutes")
        self.minutes_input.setMinimumHeight(32)
        self.minutes_input.setAlignment(Qt.AlignCenter)
        fields.addWidget(self.minutes_input)
        self.hours_input.valueChanged.connect(self._exact_duration_changed)
        self.minutes_input.valueChanged.connect(self._exact_duration_changed)
        for field in (self.hours_input, self.minutes_input):
            field.setStyleSheet(
                "QSpinBox {"
                f" background: {tokens['BG_PRIMARY']}; color: {tokens['TEXT_PRIMARY']};"
                f" border: 1px solid {tokens['BORDER_COLOR']}; border-radius: 6px; padding: 4px 6px; }}"
                "QSpinBox QLineEdit { background: transparent; border: none; padding: 0; }"
            )

        layout.addLayout(fields)

        self.duration_hint = QLabel(self)
        self.duration_hint.setWordWrap(True)
        self.duration_hint.setStyleSheet("font-size: 11px; opacity: 0.8;")
        layout.addWidget(self.duration_hint)

        layout.addStretch()

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        cancel = QPushButton("Cancel")
        cancel.setMinimumHeight(34)
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)

        confirm = self.confirm_btn = QPushButton("Add Session")
        confirm.setObjectName("primaryBtn")
        confirm.setMinimumHeight(34)
        confirm.setCursor(Qt.PointingHandCursor)
        confirm.clicked.connect(self._validate_and_accept)
        buttons.addWidget(confirm)
        layout.addLayout(buttons)

        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
            button.setDefault(False)
        confirm.setAutoDefault(True)
        confirm.setDefault(True)
        self._update_total(exact=False)

    def total_minutes(self) -> int:
        return self.hours_input.value() * 60 + self.minutes_input.value()

    def _chunks_changed(self, _checked: bool) -> None:
        if self._syncing_duration:
            return
        total = sum(
            minutes for minutes, button in self.chunk_buttons.items()
            if button.isChecked()
        )
        self._syncing_duration = True
        try:
            hours, minutes = divmod(total, 60)
            self.hours_input.setValue(hours)
            self.minutes_input.setValue(minutes)
        finally:
            self._syncing_duration = False
        self._update_total(exact=False)

    def _exact_duration_changed(self, _value: int) -> None:
        if self._syncing_duration:
            return
        remaining = self.total_minutes()
        selected: set[int] = set()
        for minutes in self.chunk_buttons:
            if remaining >= minutes:
                selected.add(minutes)
                remaining -= minutes
        # An exact value outside the chunk combinations stays authoritative.
        # The next chunk selection starts a new sum, never an old hidden sum.
        if remaining:
            selected.clear()
        self._syncing_duration = True
        try:
            for minutes, button in self.chunk_buttons.items():
                button.setChecked(minutes in selected)
        finally:
            self._syncing_duration = False
        self._update_total(exact=bool(remaining))

    def _update_total(self, *, exact: bool) -> None:
        total = self.total_minutes()
        hours, minutes = divmod(total, 60)
        duration = f"{hours}h {minutes}m" if hours else f"{minutes}m"
        self.total_label.setText(f"Total: {duration} ({total} minutes)")
        self.confirm_btn.setEnabled(total > 0)
        self.duration_hint.setText(
            "Exact duration entered. Selecting a chunk starts a new total."
            if exact else "Toggle chunks on or off. The session ends now."
        )

    def _validate_and_accept(self) -> None:
        self.hours_input.interpretText()
        self.minutes_input.interpretText()
        # Under a minute would be silently dropped by the 30-second rule.
        if self.total_minutes() < 1:
            warning(
                self,
                "Duration Too Short",
                "Enter at least one minute.",
            )
            return
        self.accept()


class SessionDialog(InlineDialog):
    # Deleting from the editor reports its own result, exactly like GoalDialog,
    # so the caller persists the removal instead of the dialog reaching into
    # the service itself.
    DELETE_RESULT = 2

    def __init__(
        self,
        parent=None,
        session=None,
        service=None,
        current_subject_id: int | None = None,
    ) -> None:
        super().__init__(parent)
        configure_window_modal(self)
        self.session = session
        self._service = service
        self._current_subject_id = current_subject_id
        self._exact_datetimes = {}
        self.subject_combo: QComboBox | None = None
        self.setWindowTitle("Edit Session" if session else "Add Session")
        show_subject_picker = session is not None and service is not None
        # Sized with ~30px of headroom over the layout's needs, so a larger
        # system font can grow a little without clipping the buttons.
        self.setFixedSize(480, 400 if show_subject_picker else 330)
        self._build_ui(show_subject_picker)

    def _build_ui(self, show_subject_picker: bool) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(10)

        heading = QLabel(self.windowTitle())
        heading.setStyleSheet("font-size: 18px; font-weight: 800;")
        layout.addWidget(heading)

        # ── Subject (edit only): move the session to another subject ─────
        if show_subject_picker:
            lbl_subject = QLabel("Subject")
            lbl_subject.setStyleSheet("font-weight: 600;")
            layout.addWidget(lbl_subject)

            self.subject_combo = QComboBox(self)
            self.subject_combo.setAccessibleName("Session subject")
            lbl_subject.setBuddy(self.subject_combo)
            self.subject_combo.setMinimumHeight(34)
            self.subject_combo.setCursor(Qt.PointingHandCursor)
            subjects = list(self._service.get_all_subjects())
            # The session's own subject must always be present (it may be
            # archived); keep it selectable so editing never strands a session.
            if self._current_subject_id is not None and all(
                s.id != self._current_subject_id for s in subjects
            ):
                current = next(
                    (
                        s
                        for s in self._service.get_all_subjects_including_archived()
                        if s.id == self._current_subject_id
                    ),
                    None,
                )
                if current is not None:
                    subjects.insert(0, current)
            for subject in subjects:
                if subject.id is None:
                    continue
                self.subject_combo.addItem(subject.name, subject.id)
            index = self.subject_combo.findData(self._current_subject_id)
            if index >= 0:
                self.subject_combo.setCurrentIndex(index)
            layout.addWidget(self.subject_combo)

        # ── Start ────────────────────────────────────────────────────────
        lbl_s = QLabel("Start Time")
        lbl_s.setStyleSheet("font-weight: 600;")
        layout.addWidget(lbl_s)

        self.start_edit = QDateTimeEdit(self)
        self.start_edit.setAccessibleName("Session start date and time")
        lbl_s.setBuddy(self.start_edit)
        self.start_edit.setDisplayFormat("yyyy-MM-dd  HH:mm:ss")
        self.start_edit.setCalendarPopup(True)
        self.start_edit.setMinimumHeight(34)
        if self.session and self.session.start_time:
            self._set_wall_clock_datetime(self.start_edit, self.session.start_time)
        else:
            # Default start = 1h ago, but never roll back to the previous day.
            now = QDateTime.currentDateTime()
            now = QDateTime(now.date(), now.time(), QTimeZone(b"UTC"))
            start_default = now.addSecs(-3600)
            if start_default.date() != now.date():
                start_default = QDateTime(now.date(), QTime(0, 0, 0), QTimeZone(b"UTC"))
            self._set_wall_clock_datetime(
                self.start_edit, timeutils.to_iso(start_default.toPython())
            )
        layout.addWidget(self.start_edit)

        # ── End ──────────────────────────────────────────────────────────
        lbl_e = QLabel("End Time")
        lbl_e.setStyleSheet("font-weight: 600;")
        layout.addWidget(lbl_e)

        self.end_edit = QDateTimeEdit(self)
        self.end_edit.setAccessibleName("Session end date and time")
        lbl_e.setBuddy(self.end_edit)
        self.end_edit.setDisplayFormat("yyyy-MM-dd  HH:mm:ss")
        self.end_edit.setCalendarPopup(True)
        self.end_edit.setMinimumHeight(34)
        if self.session and self.session.end_time:
            self._set_wall_clock_datetime(self.end_edit, self.session.end_time)
        else:
            self._set_wall_clock_datetime(
                self.end_edit, timeutils.to_iso(QDateTime.currentDateTime().toPython())
            )
        layout.addWidget(self.end_edit)

        # ── Move ─────────────────────────────────────────────────────────
        # Nudge the whole session without retyping the datetimes: both fields
        # move together, so the duration never changes. Nothing is written
        # until Save.
        lbl_m = QLabel("Move session")
        lbl_m.setStyleSheet("font-weight: 600;")
        layout.addWidget(lbl_m)
        layout.addLayout(build_move_row(self._shift))

        layout.addStretch()

        # ── Buttons ──────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        # Editing an existing session can also remove it — this is the only
        # session menu reachable from an agenda block, so delete has to live
        # here too, not just in the session lists.
        self.delete_btn: QPushButton | None = None
        if self.session is not None:
            self.delete_btn = QPushButton("✕ Delete")
            self.delete_btn.setObjectName("dangerBtn")
            self.delete_btn.setMinimumHeight(36)
            self.delete_btn.setCursor(Qt.PointingHandCursor)
            self.delete_btn.setToolTip("Delete this session permanently")
            self.delete_btn.clicked.connect(self._confirm_delete)
            btn_row.addWidget(self.delete_btn)
            btn_row.addStretch()

        cancel = QPushButton("Cancel")
        cancel.setMinimumHeight(36)
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(cancel)

        save = QPushButton("Save Session")
        save.setObjectName("primaryBtn")
        save.setMinimumHeight(36)
        save.setCursor(Qt.PointingHandCursor)
        save.clicked.connect(self._validate_and_accept)
        btn_row.addWidget(save)

        layout.addLayout(btn_row)

        for b in self.findChildren(QPushButton):
            b.setAutoDefault(False)
            b.setDefault(False)
        save.setAutoDefault(True)
        save.setDefault(True)

    def _confirm_delete(self) -> None:
        question(
            self,
            "Delete Session",
            "Delete this session permanently?",
            self._finish_confirm_delete,
        )

    def _finish_confirm_delete(self, answer) -> None:
        if answer == QMessageBox.Yes:
            self.done(self.DELETE_RESULT)

    def _shift(self, seconds: int) -> None:
        """Move both ends by the same amount (duration is preserved)."""
        try:
            start, end = timeutils.shift_session_by_seconds(
                self._wall_clock_value(self.start_edit),
                self._wall_clock_value(self.end_edit),
                seconds,
            )
        except OverflowError:
            start = end = None
        if start is None or any(
            not edit.minimumDateTime() <= QDateTime.fromString(timeutils.to_iso(value) + "Z", Qt.ISODate)
            <= edit.maximumDateTime()
            for edit, value in ((self.start_edit, start), (self.end_edit, end))
        ):
            warning(self, "Could Not Move", "That move is outside the supported calendar range.")
            return
        self._set_wall_clock_datetime(self.start_edit, timeutils.to_iso(start))
        self._set_wall_clock_datetime(self.end_edit, timeutils.to_iso(end))

    def _set_wall_clock_datetime(self, edit: QDateTimeEdit, value: str) -> None:
        # These fields represent naive wall clocks, including a DST-skipped
        # local hour. Fixed-offset Qt metadata prevents normalization; no UTC
        # conversion or timezone information enters the stored Python value.
        if hasattr(edit, "setTimeZone"):
            edit.setTimeZone(QTimeZone(b"UTC"))
        else:  # PySide6 6.6 compatibility.
            edit.setTimeSpec(Qt.UTC)
        edit.setDateTimeRange(
            QDateTime.fromString("0100-01-01T00:00:00Z", Qt.ISODate),
            QDateTime.fromString("9999-12-31T23:59:59.999Z", Qt.ISODate),
        )
        edit.setDateTime(QDateTime.fromString(value + "Z", Qt.ISODate))
        self._exact_datetimes[edit] = (timeutils.parse_iso(value), edit.dateTime().toPython())

    def _wall_clock_value(self, edit: QDateTimeEdit):
        value = edit.dateTime().toPython()
        exact, displayed = self._exact_datetimes.get(edit, (None, None))
        # Qt stores milliseconds; untouched data may have finer precision.
        return exact if exact is not None and value == displayed else value

    def _validate_and_accept(self) -> None:
        start = self._wall_clock_value(self.start_edit)
        end = self._wall_clock_value(self.end_edit)
        if start >= end:
            warning(
                self, "Invalid Times",
                "Start time must be before end time.",
            )
            return
        if timeutils.duration_seconds(start, end) < 30:
            warning(self, "Duration Too Short", "A session must last at least 30 seconds.")
            return
        self.accept()

    def get_data(self) -> dict:
        subject_id = None
        if self.subject_combo is not None:
            subject_id = self.subject_combo.currentData()
        return {
            "subject_id": subject_id,
            "start_time": self._wall_clock_value(self.start_edit),
            "end_time": self._wall_clock_value(self.end_edit),
        }
