"""
Subjects page: building, reload, and all subject/timer actions.

Mixed into MainWindow. Relies on shared attributes/methods provided by the host
window: ``self.service``, ``self._tokens``, ``self._pages``, ``self._reload_graphs``,
``self._open_settings``.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QVBoxLayout, QWidget,
)

from .widgets.active_timer import ActiveTimerWidget
from .widgets.delete_subject_dialog import (
    DeleteSubjectDialog, RESULT_ARCHIVE, RESULT_DELETE,
)
from .widgets.dialog_utils import open_dialog, question, warning
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
        self._archive_toggle_btn.clicked.connect(
            lambda: self._queue_ui_action(self._toggle_archived_view)
        )
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
        self._filter.currentTextChanged.connect(
            lambda _text: self._queue_ui_action(self._reload_subjects)
        )
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
        self._pending_tracking_feedback_id: int | None = None
        self._tracking_refresh_timer = QTimer(self)
        self._tracking_refresh_timer.setSingleShot(True)
        self._tracking_refresh_timer.timeout.connect(
            self._apply_tracking_refresh
        )

        self._pages.addWidget(page)

    def _reload_subjects(self) -> None:
        rendered_view = getattr(
            self, "_subjects_rendered_view", self._showing_archived
        )
        self._subject_view_states[rendered_view] = (
            self._subjects_list.capture_view_state()
        )
        restore_state = self._subject_view_states.get(self._showing_archived)
        self._subjects_rendered_view = self._showing_archived

        is_tracking = self.service.active_session is not None
        if is_tracking and self.service.active_subject:
            self._timer.set_active(self.service.active_subject, self.service.active_session)
        else:
            self._timer.clear()

        subjects = self.service.get_all_subjects(archived=self._showing_archived)
        if not subjects:
            self._subjects_list.clear_cards()
            self._subjects_empty.show()
            self._subjects_list.hide()
            if hasattr(self, "_sync_active_session_indicator"):
                self._sync_active_session_indicator()
            return
        self._subjects_empty.hide()
        self._subjects_list.show()

        filter_type = self._filter.currentText()
        stats_map = self.service.get_subject_stats_map(filter_type)
        subjects_by_id = {s.id: s for s in subjects if s.id is not None}
        ids = list(subjects_by_id)
        # Structural fingerprint: a change here rebuilds just that one card. The
        # active/dimmed state and tracked total are transient and refresh in
        # place, so start/stop, archiving, and adding sessions destroy nothing.
        signatures = {
            sid: (s.name, s.color, s.notes or "", bool(s.is_archived))
            for sid, s in subjects_by_id.items()
        }

        def _active_id():
            return (
                self.service.active_subject.id
                if is_tracking and self.service.active_subject
                else None
            )

        def make_card(sid: int) -> SubjectItemWidget:
            subject = subjects_by_id[sid]
            is_active = sid == _active_id()
            card = SubjectItemWidget(
                subject,
                self._tokens,
                total_seconds=stats_map.get(sid, 0),
                is_dimmed=bool(is_tracking and not is_active),
                is_active=is_active,
                is_archived=bool(subject.is_archived),
                shortcut_number=None if self._showing_archived else 1,
                parent=self._subjects_list.viewport(),
            )
            card.start_requested.connect(self._start_tracking)
            card.edit_requested.connect(self._edit_subject)
            card.delete_requested.connect(self._delete_subject)
            card.manage_sessions_requested.connect(self._manage_sessions)
            card.archive_requested.connect(
                lambda sid: self._queue_ui_action(self._archive_subject, sid)
            )
            card.unarchive_requested.connect(
                lambda sid: self._queue_ui_action(self._unarchive_subject, sid)
            )
            return card

        def update_card(sid: int, card: SubjectItemWidget) -> None:
            is_active = sid == _active_id()
            card.update_stats(stats_map.get(sid, 0))
            card.set_active(is_active)
            card.set_dimmed(bool(is_tracking and not is_active))

        self._subjects_list.sync_cards(ids, signatures, make_card, update_card)
        self._subjects_list.restore_view_state(restore_state)
        if hasattr(self, "_sync_active_session_indicator"):
            self._sync_active_session_indicator()

    # ── Subject actions ─────────────────────────────────────────────────
    def _new_subject(self) -> None:
        existing = [s.color for s in self.service.get_all_subjects(archived=False)]
        dlg = SubjectDialog(self, existing_colors=existing)
        open_dialog(dlg, self._finish_new_subject)

    def _finish_new_subject(self, result: int, dialog: QDialog) -> None:
        if result != QDialog.Accepted:
            return
        d = dialog.get_data()
        if not d["name"]:
            warning(self, "Validation", "Subject name cannot be empty.")
            return
        if not self.service.add_subject(d["name"], d["color"], d["notes"]):
            warning(
                self,
                "Duplicate Name",
                f'A subject named "{d["name"]}" already exists.',
            )
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

        open_dialog(
            dlg,
            lambda result, dialog: self._finish_edit_subject(
                subject_id, result, dialog
            ),
        )

    def _finish_edit_subject(
        self, subject_id: int, result: int, dialog: QDialog
    ) -> None:
        if result != QDialog.Accepted:
            return
        d = dialog.get_data()
        if not d["name"]:
            warning(self, "Validation", "Subject name cannot be empty.")
            return
        if not self.service.update_subject(
            subject_id, d["name"], d["color"], d["notes"]
        ):
            warning(
                self,
                "Duplicate Name",
                f'A subject named "{d["name"]}" already exists.',
            )
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
            question(
                self,
                "Delete Subject",
                f'Delete "{name}"? It has no tracked sessions.',
                lambda answer: self._finish_delete_empty_subject(
                    subject_id, answer
                ),
            )
            return

        # Has tracked time -> strong confirmation, archive recommended.
        dlg = DeleteSubjectDialog(name, summary, self)
        open_dialog(
            dlg,
            lambda result, _dialog: self._finish_delete_subject(
                subject_id, result
            ),
        )

    def _finish_delete_empty_subject(
        self, subject_id: int, answer: QMessageBox.StandardButton
    ) -> None:
        if answer != QMessageBox.Yes:
            return
        self.service.delete_subject(subject_id)
        self._reload_subjects()
        self._reload_graphs()

    def _finish_delete_subject(self, subject_id: int, result: int) -> None:
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
        dialog = ManageSessionsDialog(subject_id, self.service, self)
        open_dialog(
            dialog,
            lambda _result, _dialog: self._finish_manage_sessions(),
        )

    def _finish_manage_sessions(self) -> None:
        self._reload_subjects()
        self._reload_graphs()

    # ── Timer ───────────────────────────────────────────────────────────
    def _start_tracking(
        self, subject_id: int, shortcut_feedback: bool = False
    ) -> None:
        if self.service.active_session is not None:
            # Something is already being tracked: offer to switch instead of
            # silently ignoring the click/key. The running session keeps
            # ticking until the switch is confirmed, so a misclick costs nothing.
            self._confirm_switch(subject_id, shortcut_feedback)
            return
        if self.service.start_subject(subject_id):
            self._pending_tracking_feedback_id = (
                subject_id if shortcut_feedback else None
            )
            # Let the originating mouse/key event finish before rebuilding the
            # card list. Destroying or hiding the event source synchronously can
            # make a fullscreen macOS window lose activation.
            self._tracking_refresh_timer.start(0)

    def _confirm_switch(
        self, subject_id: int, shortcut_feedback: bool = False
    ) -> None:
        active = self.service.active_subject
        if active is None or active.id == subject_id:
            return
        target = next(
            (
                s
                for s in self.service.get_all_subjects(archived=False)
                if s.id == subject_id
            ),
            None,
        )
        if target is None:
            return

        def finish(answer: QMessageBox.StandardButton) -> None:
            if answer != QMessageBox.Yes:
                return
            if self.service.switch_subject(subject_id):
                self._pending_tracking_feedback_id = (
                    subject_id if shortcut_feedback else None
                )
                self._tracking_refresh_timer.start(0)

        question(
            self,
            "Switch Subject",
            f'Stop "{active.name}" and start tracking "{target.name}"?',
            finish,
        )

    def _stop_tracking(self) -> None:
        self.service.stop_active_subject()
        self._pending_tracking_feedback_id = None
        # The Stop button sits on the timer page that the refresh hides. Defer
        # that page switch until its click event has returned to Qt.
        self._tracking_refresh_timer.start(0)

    def _update_tracking_state_inplace(self) -> bool:
        """Reflect a start/stop by mutating existing cards instead of rebuilding.

        A start/stop never changes which subjects exist or their order — only
        the active/dimmed state, the timer, and the finished subject's total.
        Rebuilding the whole card list here is what trips the macOS fullscreen
        compositor into a Space glitch, so keep the widget tree intact.
        Returns False (caller falls back to a full reload) if the live cards no
        longer match the current subjects.
        """
        if self._showing_archived:
            return False
        subjects = self.service.get_all_subjects(archived=False)
        if not subjects:
            return False
        ids = [s.id for s in subjects if s.id is not None]
        if ids != self._subjects_list.ordered_ids():
            return False

        is_tracking = self.service.active_session is not None
        active = self.service.active_subject
        if is_tracking and active:
            self._timer.set_active(active, self.service.active_session)
        else:
            self._timer.clear()

        stats_map = self.service.get_subject_stats_map(self._filter.currentText())
        for subject in subjects:
            card = self._subjects_list.card_widget(subject.id)
            if card is None:
                return False
            card.update_stats(stats_map.get(subject.id, 0))
            is_active = bool(is_tracking and active and subject.id == active.id)
            card.set_active(is_active)
            card.set_dimmed(bool(is_tracking and not is_active))

        if hasattr(self, "_sync_active_session_indicator"):
            self._sync_active_session_indicator()
        return True

    def _apply_tracking_refresh(self) -> None:
        if not self._update_tracking_state_inplace():
            self._reload_subjects()
        feedback_id = self._pending_tracking_feedback_id
        self._pending_tracking_feedback_id = None
        if (
            feedback_id is not None
            and self.service.active_subject is not None
            and self.service.active_subject.id == feedback_id
        ):
            self._subjects_list.pulse_card(feedback_id)

        # Starting/stopping is only available from Subjects. Avoid redrawing a
        # hidden chart; entering Graphs already performs a fresh reload.
        if self._pages.currentIndex() == 2:
            self._reload_graphs()
