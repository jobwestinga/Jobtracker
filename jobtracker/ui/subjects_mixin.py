"""
Subjects page: building, reload, and all subject/timer actions.

Mixed into MainWindow. Relies on shared attributes/methods provided by the host
window: ``self.service``, ``self._tokens``, ``self._pages``, ``self._reload_graphs``,
``self._open_settings``.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from .widgets.active_timer import ActiveTimerWidget
from .widgets.delete_subject_dialog import (
    DeleteSubjectDialog, RESULT_ARCHIVE, RESULT_DELETE,
)
from .widgets.manage_sessions_dialog import ManageSessionsDialog
from .widgets.reorderable_list import ReorderableCardList
from .widgets.subject_dialog import SubjectDialog
from .widgets.subject_item import SubjectItemWidget


class SubjectsMixin:
    def _build_subjects_page(self) -> None:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("Subjects")
        title.setObjectName("title")
        header.addWidget(title)
        header.addStretch()

        self._add_subject_btn = QPushButton("+ Subject")
        self._add_subject_btn.setObjectName("primaryBtn")
        self._add_subject_btn.setMinimumHeight(34)
        self._add_subject_btn.setCursor(Qt.PointingHandCursor)
        self._add_subject_btn.clicked.connect(self._new_subject)
        header.addWidget(self._add_subject_btn)

        self._archive_toggle_btn = QPushButton("Archived")
        self._archive_toggle_btn.setMinimumHeight(34)
        self._archive_toggle_btn.setCursor(Qt.PointingHandCursor)
        self._archive_toggle_btn.clicked.connect(self._toggle_archived_view)
        header.addWidget(self._archive_toggle_btn)

        gear_btn = QPushButton("⚙")
        gear_btn.setObjectName("gearBtn")
        gear_btn.setMinimumHeight(34)
        gear_btn.setCursor(Qt.PointingHandCursor)
        gear_btn.setToolTip("Settings")
        gear_btn.clicked.connect(self._open_settings)
        header.addWidget(gear_btn)

        lay.addLayout(header)

        self._timer = ActiveTimerWidget()
        self._timer.stop_requested.connect(self._stop_tracking)
        lay.addWidget(self._timer)

        stats_hdr = QHBoxLayout()
        stats_lbl = QLabel("Tracked Time")
        stats_lbl.setObjectName("tasksLabel")
        stats_hdr.addWidget(stats_lbl)
        stats_hdr.addStretch()

        self._filter = QComboBox()
        self._filter.addItems(["Total", "Today", "Last 7 days", "Last 30 days"])
        self._filter.setCursor(Qt.PointingHandCursor)
        self._filter.currentTextChanged.connect(lambda: self._reload_subjects())
        stats_hdr.addWidget(self._filter)
        lay.addLayout(stats_hdr)

        self._subjects_empty = QLabel("No subjects yet - click + Subject to begin.")
        self._subjects_empty.setStyleSheet(
            f"color: {self._tokens['TEXT_DIMMED']}; font-style: italic; padding: 26px 0;"
        )
        self._subjects_empty.setAlignment(Qt.AlignCenter)
        self._subjects_empty.hide()
        lay.addWidget(self._subjects_empty)

        self._subjects_list = ReorderableCardList(spacing=8)
        self._subjects_list.order_changed.connect(self._on_subject_order_changed)
        lay.addWidget(self._subjects_list, 1)
        self._subject_view_states = {False: None, True: None}
        self._subjects_rendered_view = False

        self._pages.addWidget(page)

    def _reload_subjects(self) -> None:
        rendered_view = getattr(
            self, "_subjects_rendered_view", self._showing_archived
        )
        self._subject_view_states[rendered_view] = (
            self._subjects_list.capture_view_state()
        )
        restore_state = self._subject_view_states.get(self._showing_archived)
        self._subjects_list.clear_cards()
        self._subjects_rendered_view = self._showing_archived

        is_tracking = self.service.active_session is not None
        if is_tracking and self.service.active_subject:
            self._timer.set_active(self.service.active_subject, self.service.active_session)
        else:
            self._timer.clear()

        subjects = self.service.get_all_subjects(archived=self._showing_archived)
        if not subjects:
            self._subjects_empty.show()
            self._subjects_list.hide()
            if hasattr(self, "_sync_active_session_indicator"):
                self._sync_active_session_indicator()
            return
        self._subjects_empty.hide()
        self._subjects_list.show()

        filter_type = self._filter.currentText()
        for index, subject in enumerate(subjects, start=1):
            if subject.id is None:
                continue
            total = self.service.get_subject_stats(subject.id, filter_type)
            is_active = bool(
                is_tracking
                and self.service.active_subject
                and subject.id == self.service.active_subject.id
            )
            dimmed = bool(is_tracking and not is_active)

            card = SubjectItemWidget(
                subject,
                self._tokens,
                total_seconds=total,
                is_dimmed=dimmed,
                is_active=is_active,
                is_archived=bool(subject.is_archived),
                shortcut_number=(
                    index
                    if not self._showing_archived
                    else None
                ),
            )
            card.start_requested.connect(self._start_tracking)
            card.edit_requested.connect(self._edit_subject)
            card.delete_requested.connect(self._delete_subject)
            card.manage_sessions_requested.connect(self._manage_sessions)
            card.archive_requested.connect(self._archive_subject)
            card.unarchive_requested.connect(self._unarchive_subject)
            self._subjects_list.add_card(subject.id, card)
        self._subjects_list.restore_view_state(restore_state)
        if hasattr(self, "_sync_active_session_indicator"):
            self._sync_active_session_indicator()

    # ── Subject actions ─────────────────────────────────────────────────
    def _new_subject(self) -> None:
        existing = [s.color for s in self.service.get_all_subjects(archived=False)]
        dlg = SubjectDialog(self, existing_colors=existing)
        if dlg.exec():
            d = dlg.get_data()
            if not d["name"]:
                QMessageBox.warning(self, "Validation", "Subject name cannot be empty.")
                return
            if not self.service.add_subject(d["name"], d["color"], d["notes"]):
                QMessageBox.warning(self, "Duplicate Name", f'A subject named "{d["name"]}" already exists.')
                return
            self._reload_subjects()
            self._reload_graphs()

    def _toggle_archived_view(self) -> None:
        self._showing_archived = not self._showing_archived
        if self._showing_archived:
            self._archive_toggle_btn.setText("Back to Active")
            self._archive_toggle_btn.setObjectName("primaryBtn")
            self._add_subject_btn.hide()
            self._timer.hide()
        else:
            self._archive_toggle_btn.setText("Archived")
            self._archive_toggle_btn.setObjectName("")
            self._add_subject_btn.show()
            self._timer.show()

        # Re-apply styles if object name changed
        self._archive_toggle_btn.style().unpolish(self._archive_toggle_btn)
        self._archive_toggle_btn.style().polish(self._archive_toggle_btn)

        self._reload_subjects()

    def _archive_subject(self, subject_id: int) -> None:
        self.service.archive_subject(subject_id)
        if hasattr(self, "_register_undo"):
            self._register_undo(
                lambda sid=subject_id: self.service.unarchive_subject(sid)
            )
        self._reload_subjects()
        self._reload_graphs()

    def _unarchive_subject(self, subject_id: int) -> None:
        self.service.unarchive_subject(subject_id)
        if hasattr(self, "_register_undo"):
            self._register_undo(
                lambda sid=subject_id: self.service.archive_subject(sid)
            )
        self._reload_subjects()
        self._reload_graphs()

    def _edit_subject(self, subject_id: int) -> None:
        subject = next((s for s in self.service.get_all_subjects() if s.id == subject_id), None)
        if not subject:
            return

        existing = [s.color for s in self.service.get_all_subjects(archived=False) if s.id != subject_id]
        dlg = SubjectDialog(self, existing_colors=existing)
        dlg.setWindowTitle("Edit Subject")
        dlg.name_input.setText(subject.name)
        dlg.selected_color = subject.color
        dlg._refresh_swatches()
        if subject.notes:
            dlg.notes_input.setPlainText(subject.notes)

        if dlg.exec():
            d = dlg.get_data()
            if not d["name"]:
                QMessageBox.warning(self, "Validation", "Subject name cannot be empty.")
                return
            if not self.service.update_subject(subject_id, d["name"], d["color"], d["notes"]):
                QMessageBox.warning(self, "Duplicate Name", f'A subject named "{d["name"]}" already exists.')
                return
            self._reload_subjects()
            self._reload_graphs()

    def _delete_subject(self, subject_id: int) -> None:
        summary = self.service.get_subject_deletion_summary(subject_id)
        subject = next(
            (s for s in self.service.get_all_subjects_including_archived() if s.id == subject_id),
            None,
        )
        name = subject.name if subject else "this subject"

        # No history to lose -> a simple confirm is enough (don't be annoying).
        if summary["session_count"] == 0:
            if QMessageBox.question(
                self,
                "Delete Subject",
                f'Delete "{name}"? It has no tracked sessions.',
                QMessageBox.Yes | QMessageBox.No,
            ) == QMessageBox.Yes:
                self.service.delete_subject(subject_id)
                self._reload_subjects()
                self._reload_graphs()
            return

        # Has tracked time -> strong confirmation, archive recommended.
        dlg = DeleteSubjectDialog(name, summary, self)
        result = dlg.exec()
        if result == RESULT_DELETE:
            self.service.delete_subject(subject_id)
        elif result == RESULT_ARCHIVE:
            self.service.archive_subject(subject_id)
        else:
            return
        self._reload_subjects()
        self._reload_graphs()

    def _on_subject_order_changed(self, ordered_ids: list[int]) -> None:
        archived = self._showing_archived
        previous = [
            subject.id
            for subject in self.service.get_all_subjects(archived=archived)
            if subject.id is not None
        ]
        if previous == ordered_ids:
            return
        self.service.set_subject_order(
            ordered_ids, archived=archived
        )
        if hasattr(self, "_register_undo"):
            self._register_undo(
                lambda old=previous, archived_view=archived: (
                    self.service.set_subject_order(
                        old, archived=archived_view
                    )
                )
            )

    def _manage_sessions(self, subject_id: int) -> None:
        ManageSessionsDialog(subject_id, self.service, self).exec()
        self._reload_subjects()
        self._reload_graphs()

    # ── Timer ───────────────────────────────────────────────────────────
    def _start_tracking(
        self, subject_id: int, shortcut_feedback: bool = False
    ) -> None:
        if self.service.start_subject(subject_id):
            self._reload_subjects()
            if shortcut_feedback:
                self._subjects_list.pulse_card(subject_id)
            self._reload_graphs()

    def _stop_tracking(self) -> None:
        self.service.stop_active_subject()
        self._reload_subjects()
        self._reload_graphs()
