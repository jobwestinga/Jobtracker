"""
Main application window with 3 environments:
- Subjects
- Tasks
- Graphs
"""

from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta

from PySide6.QtCore import QEvent, QTimer, Qt, QRect
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedLayout,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.database import db
from ..core.themes import get_tokens
from ..core.timeutils import parse_iso, logical_day, agenda_hour_label
from ..services.tracker_service import TrackerService
from .widgets.delete_subject_dialog import (
    DeleteSubjectDialog, RESULT_ARCHIVE, RESULT_DELETE,
)
from .widgets.recovery_dialog import RecoveryDialog

logger = logging.getLogger("jobtracker")
from .styles import build_stylesheet
from .widgets.active_timer import ActiveTimerWidget
from .widgets.agenda_view import AgendaViewWidget
from .widgets.fx_background import FxBackgroundWidget
from .widgets.graph_settings_dialog import GraphSettingsDialog
from .widgets.graphs_view import WorkGraphWidget
from .widgets.manage_sessions_dialog import ManageSessionsDialog
from .widgets.reorderable_list import ReorderableCardList
from .widgets.settings_dialog import SettingsDialog
from .widgets.subject_dialog import SubjectDialog
from .widgets.subject_item import SubjectItemWidget
from .widgets.todo_task_dialog import TodoTaskDialog
from .widgets.todo_task_item import TodoTaskItemWidget


# ── Badge button ─────────────────────────────────────────────────────────────

class _BadgeNavButton(QPushButton):
    """Navigation button with an optional numeric badge circle."""

    def __init__(self, text: str, parent=None) -> None:
        super().__init__(text, parent)
        self._badge_count = 0

    def set_badge_count(self, count: int) -> None:
        self._badge_count = max(0, count)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if self._badge_count <= 0:
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        text = str(self._badge_count) if self._badge_count <= 99 else "99+"
        font = QFont("SF Pro Text", 9, QFont.Bold)
        p.setFont(font)
        fm = p.fontMetrics()
        text_width = fm.horizontalAdvance(text)
        badge_h = max(18, fm.height() + 2)
        badge_w = max(badge_h, text_width + 10)

        x = self.width() - badge_w - 6
        y = (self.height() - badge_h) // 2

        # Draw badge circle / pill
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#EF4444"))
        p.drawRoundedRect(QRect(x, y, badge_w, badge_h), badge_h // 2, badge_h // 2)

        # Draw text
        p.setPen(QColor("#FFFFFF"))
        p.drawText(QRect(x, y, badge_w, badge_h), Qt.AlignCenter, text)
        p.end()


class MainWindow(QMainWindow):
    def __init__(self, app_instance) -> None:
        super().__init__()
        self.app_instance = app_instance
        self.service = TrackerService()
        self.setWindowTitle("JobTracker")
        self.setMinimumSize(560, 760)
        self.resize(680, 920)

        self._fx = db.get_setting("theme_fx", "Glow")
        self._palette = db.get_setting("theme_palette", "Ocean")
        self._tokens = get_tokens(self._fx, self._palette)

        # Graph settings (persisted)
        self._graph_range_days: int | None = self._load_graph_range()
        self._graph_view_mode: str = db.get_setting("graph_view_mode", "bar")
        self._graph_hour_start: int = int(db.get_setting("graph_hour_start", "6"))
        self._graph_hour_end: int = int(db.get_setting("graph_hour_end", "23"))
        self._graph_grouping: str = db.get_setting("graph_grouping", "daily")
        self._graph_custom_range: tuple[date, date] | None = self._load_custom_range()

        self._showing_archived: bool = False

        self.app_instance.aboutToQuit.connect(self._on_close)
        # Pause expensive background animation when the app is not in front.
        self.app_instance.applicationStateChanged.connect(self._on_app_state_changed)

        self._build_ui()
        self._apply_theme()
        self._reload()

        # Keep graphs up to date while tracking, without requiring manual refresh.
        self._graph_live_timer = QTimer(self)
        self._graph_live_timer.setInterval(2000)
        self._graph_live_timer.timeout.connect(self._refresh_graphs_if_needed)
        self._graph_live_timer.start()

        # Heartbeat: record that the active session is still legitimately running
        # roughly once a minute. One cheap UPDATE, no history rows. Used by future
        # crash / ghost-time recovery so we know when the app was last alive.
        self._heartbeat_timer = QTimer(self)
        self._heartbeat_timer.setInterval(60_000)
        self._heartbeat_timer.timeout.connect(self._heartbeat)
        self._heartbeat_timer.start()

        # After the window is up, offer recovery for any unfinished session.
        QTimer.singleShot(0, self._maybe_prompt_recovery)

    def _load_custom_range(self) -> tuple[date, date] | None:
        start_raw = db.get_setting("graph_custom_start", "")
        end_raw = db.get_setting("graph_custom_end", "")
        if db.get_setting("graph_range", "7") != "custom" or not start_raw or not end_raw:
            return None
        try:
            return date.fromisoformat(start_raw), date.fromisoformat(end_raw)
        except ValueError:
            return None

    def _maybe_prompt_recovery(self) -> None:
        info = self.service.get_active_recovery_info()
        if not info:
            return
        dlg = RecoveryDialog(info, self)
        if dlg.exec():
            self.service.resolve_recovery(dlg.chosen_end())
        # If dismissed, the session stays active (non-destructive) and keeps ticking.
        self._reload_subjects()
        self._reload_graphs()

    def _on_app_state_changed(self, state) -> None:
        active = state == Qt.ApplicationActive
        if hasattr(self, "_fx_bg"):
            self._fx_bg.set_animating(active)
        if hasattr(self, "_graph_live_timer"):
            if active:
                if not self._graph_live_timer.isActive():
                    self._graph_live_timer.start()
            else:
                self._graph_live_timer.stop()

    def changeEvent(self, event) -> None:  # noqa: N802
        if event.type() == QEvent.WindowStateChange and hasattr(self, "_fx_bg"):
            minimized = bool(self.windowState() & Qt.WindowMinimized)
            self._fx_bg.set_animating(not minimized)
        super().changeEvent(event)

    def _heartbeat(self) -> None:
        try:
            self.service.heartbeat_active_session()
        except Exception:
            logger.exception("Heartbeat failed")

    def _load_graph_range(self) -> int | None:
        raw = db.get_setting("graph_range", "7")
        if raw == "all":
            return None
        try:
            return int(raw)
        except ValueError:
            return 7

    # ═══════════════════════════════════════════════════════════════════
    #  UI
    # ═══════════════════════════════════════════════════════════════════
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)

        self._layered = QStackedLayout(central)
        self._layered.setStackingMode(QStackedLayout.StackAll)
        self._layered.setContentsMargins(0, 0, 0, 0)

        self._fx_bg = FxBackgroundWidget()
        self._layered.addWidget(self._fx_bg)

        content = QWidget()
        content.setAttribute(Qt.WA_StyledBackground, True)
        self._layered.addWidget(content)
        # Keep app content above the animated FX background.
        self._layered.setCurrentWidget(content)

        root = QVBoxLayout(content)
        root.setContentsMargins(18, 16, 18, 12)
        root.setSpacing(10)

        self._pages = QStackedWidget()
        root.addWidget(self._pages, 1)

        self._build_subjects_page()
        self._build_tasks_page()
        self._build_graphs_page()

        root.addWidget(self._build_bottom_nav())
        self._switch_page(0)

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

        self._pages.addWidget(page)

    def _build_tasks_page(self) -> None:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("Tasks")
        title.setObjectName("title")
        header.addWidget(title)
        header.addStretch()

        add_btn = QPushButton("+ Task")
        add_btn.setObjectName("primaryBtn")
        add_btn.setMinimumHeight(34)
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.clicked.connect(self._new_todo_task)
        header.addWidget(add_btn)

        gear_btn = QPushButton("⚙")
        gear_btn.setObjectName("gearBtn")
        gear_btn.setMinimumHeight(34)
        gear_btn.setCursor(Qt.PointingHandCursor)
        gear_btn.setToolTip("Settings")
        gear_btn.clicked.connect(self._open_settings)
        header.addWidget(gear_btn)

        lay.addLayout(header)

        tools = QHBoxLayout()
        tools.addStretch()
        sort_deadline_btn = QPushButton("Sort by Deadline")
        sort_deadline_btn.setCursor(Qt.PointingHandCursor)
        sort_deadline_btn.setMinimumHeight(30)
        sort_deadline_btn.clicked.connect(self._sort_todo_by_deadline)
        tools.addWidget(sort_deadline_btn)
        lay.addLayout(tools)

        self._todo_empty = QLabel("No tasks yet - click + Task to add one.")
        self._todo_empty.setStyleSheet(
            f"color: {self._tokens['TEXT_DIMMED']}; font-style: italic; padding: 20px 0;"
        )
        self._todo_empty.setAlignment(Qt.AlignCenter)
        self._todo_empty.hide()
        lay.addWidget(self._todo_empty)

        self._todo_list = ReorderableCardList(spacing=8)
        self._todo_list.order_changed.connect(self._on_todo_order_changed)
        lay.addWidget(self._todo_list, 1)

        self._pages.addWidget(page)

    def _build_graphs_page(self) -> None:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("Graphs")
        title.setObjectName("title")
        header.addWidget(title)
        header.addStretch()

        graph_settings_btn = QPushButton("⚙ Graph Settings")
        graph_settings_btn.setObjectName("primaryBtn")
        graph_settings_btn.setMinimumHeight(34)
        graph_settings_btn.setCursor(Qt.PointingHandCursor)
        graph_settings_btn.clicked.connect(self._open_graph_settings)
        header.addWidget(graph_settings_btn)

        gear_btn = QPushButton("⚙")
        gear_btn.setObjectName("gearBtn")
        gear_btn.setMinimumHeight(34)
        gear_btn.setCursor(Qt.PointingHandCursor)
        gear_btn.setToolTip("Settings")
        gear_btn.clicked.connect(self._open_settings)
        header.addWidget(gear_btn)

        lay.addLayout(header)

        self._graph_subtitle = QLabel("")
        self._graph_subtitle.setStyleSheet("font-size: 12px; font-weight: 500;")
        lay.addWidget(self._graph_subtitle)

        # Stacked widget to swap between bar chart and agenda view
        self._graph_stack = QStackedWidget()

        self._graph_view = WorkGraphWidget()
        self._graph_stack.addWidget(self._graph_view)  # index 0

        self._agenda_view = AgendaViewWidget()
        self._graph_stack.addWidget(self._agenda_view)  # index 1

        lay.addWidget(self._graph_stack, 1)

        self._graph_legend = QLabel("")
        self._graph_legend.setWordWrap(True)
        self._graph_legend.setStyleSheet("font-size: 11px;")
        lay.addWidget(self._graph_legend)

        self._pages.addWidget(page)

    def _build_bottom_nav(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("bottomNav")
        bar.setFixedHeight(54)

        nav = QHBoxLayout(bar)
        nav.setContentsMargins(0, 0, 0, 0)
        nav.setSpacing(0)

        labels = ["Subjects", "Tasks", "Graphs"]
        self._nav_buttons: list[QPushButton] = []

        for idx, label in enumerate(labels):
            if label == "Tasks":
                btn = _BadgeNavButton(label)
                self._tasks_nav_btn = btn
            else:
                btn = QPushButton(label)
            btn.setObjectName("navBtn")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda checked, i=idx: self._switch_page(i))
            nav.addWidget(btn, 1)
            self._nav_buttons.append(btn)

        return bar

    def _switch_page(self, index: int) -> None:
        self._pages.setCurrentIndex(index)
        for i, btn in enumerate(self._nav_buttons):
            btn.setChecked(i == index)
        if index == 2:
            self._reload_graphs()

    # ═══════════════════════════════════════════════════════════════════
    #  THEME
    # ═══════════════════════════════════════════════════════════════════
    def _apply_theme(self) -> None:
        self._tokens = get_tokens(self._fx, self._palette)
        self.app_instance.setStyleSheet(build_stylesheet(self._tokens))
        self._timer.apply_tokens(self._tokens)
        self._graph_view.set_tokens(self._tokens)
        self._agenda_view.set_tokens(self._tokens)
        self._fx_bg.apply_theme(self._tokens, self._fx)

    # ═══════════════════════════════════════════════════════════════════
    #  RELOAD
    # ═══════════════════════════════════════════════════════════════════
    def _reload(self) -> None:
        self._reload_subjects()
        self._reload_tasks()
        self._reload_graphs()

    def _reload_subjects(self) -> None:
        self._subjects_list.clear_cards()

        is_tracking = self.service.active_session is not None
        if is_tracking and self.service.active_subject:
            self._timer.set_active(self.service.active_subject, self.service.active_session)
        else:
            self._timer.clear()

        subjects = self.service.get_all_subjects(archived=self._showing_archived)
        if not subjects:
            self._subjects_empty.show()
            self._subjects_list.hide()
            return
        self._subjects_empty.hide()
        self._subjects_list.show()

        filter_type = self._filter.currentText()
        for subject in subjects:
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
            )
            card.start_requested.connect(self._start_tracking)
            card.edit_requested.connect(self._edit_subject)
            card.delete_requested.connect(self._delete_subject)
            card.manage_sessions_requested.connect(self._manage_sessions)
            card.archive_requested.connect(self._archive_subject)
            card.unarchive_requested.connect(self._unarchive_subject)
            self._subjects_list.add_card(subject.id, card)

    def _reload_tasks(self) -> None:
        self._todo_list.clear_cards()

        todo_tasks = self.service.get_all_todo_tasks()
        if not todo_tasks:
            self._todo_empty.show()
            self._todo_list.hide()
            self._update_task_badge(0)
            return
        self._todo_empty.hide()
        self._todo_list.show()

        for todo_task in todo_tasks:
            card = TodoTaskItemWidget(todo_task, self._tokens)
            card.edit_requested.connect(self._edit_todo_task)
            card.delete_requested.connect(self._delete_todo_task)
            card.complete_requested.connect(self._complete_todo_task)
            if todo_task.id is not None:
                self._todo_list.add_card(todo_task.id, card)

        # Update badge
        count = self.service.get_incomplete_todo_count()
        self._update_task_badge(count)

    def _update_task_badge(self, count: int) -> None:
        if hasattr(self, "_tasks_nav_btn"):
            self._tasks_nav_btn.set_badge_count(count)

    def _reload_graphs(self) -> None:
        if self._graph_view_mode == "agenda":
            self._reload_agenda()
        else:
            self._reload_bar_chart()

    def _current_window(self) -> tuple[int | None, date | None, date | None]:
        """(days, start_date, end_date) for analytics: a custom range wins."""
        if self._graph_custom_range:
            start_day, end_day = self._graph_custom_range
            return None, start_day, end_day
        return self._graph_range_days, None, None

    def _range_label(self) -> str:
        if self._graph_custom_range:
            start_day, end_day = self._graph_custom_range
            return f"{start_day.isoformat()} → {end_day.isoformat()}"
        return f"{self._graph_range_days} days" if self._graph_range_days else "All Time"

    def _set_legend(self, seen: dict[str, str]) -> None:
        if seen:
            parts = [
                f"<span style='color:{color};'>■</span> {name}"
                for name, color in seen.items()
            ]
            self._graph_legend.setText("&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;".join(parts))
        else:
            self._graph_legend.setText("No tracked sessions in the displayed range.")

    def _reload_bar_chart(self) -> None:
        self._graph_stack.setCurrentIndex(0)
        days, start_day, end_day = self._current_window()
        data = self.service.get_subject_breakdown(
            grouping=self._graph_grouping, days=days,
            start_date=start_day, end_date=end_day,
        )
        fit_width = db.get_setting("graph_fit_horizontal", "1") == "1"
        self._graph_view.set_data(data, fit_width)

        total_seconds = sum(day["total_seconds"] for day in data)
        group_label = {"daily": "Daily", "weekly": "Weekly", "monthly": "Monthly"}.get(
            self._graph_grouping, "Daily"
        )
        self._graph_subtitle.setText(
            f"{group_label} · {self._range_label()} · {total_seconds / 3600:.1f}h"
        )

        seen: dict[str, str] = {}
        for day in data:
            for seg in day["segments"]:
                seen[seg["subject_name"]] = seg["color"]
        self._set_legend(seen)

    def _reload_agenda(self) -> None:
        self._graph_stack.setCurrentIndex(1)

        day_start = self.service.get_day_start()
        today = logical_day(datetime.now(), day_start)

        if self._graph_custom_range:
            start_day, end_day = self._graph_custom_range
            if end_day < start_day:
                start_day, end_day = end_day, start_day
        elif self._graph_range_days is None:
            earliest_iso = db.get_earliest_session_date()
            parsed = parse_iso(earliest_iso) if earliest_iso else None
            start_day = logical_day(parsed, day_start) if parsed else today
            end_day = today
        else:
            end_day = today
            start_day = today - timedelta(days=max(1, self._graph_range_days) - 1)

        day_keys, sessions = self.service.get_agenda_data(start_day, end_day, day_start)

        # Auto-fit the visible hour band to actual work (in agenda-hour space, so
        # late-night work can push the bottom edge past 24 -> "(+1)").
        hour_start = self._graph_hour_start
        hour_end = self._graph_hour_end
        if db.get_setting("graph_autofit_hours", "0") == "1" and sessions:
            try:
                min_h = math.floor(min(s["start_h"] for s in sessions))
                max_h = math.ceil(max(s["end_h"] for s in sessions))
                if min_h < max_h:
                    hour_start = max(0, min_h)
                    hour_end = min(27, max(max_h, hour_start + 1))
            except Exception:
                logger.exception("Agenda auto-fit failed; using configured hours")

        fit_width = db.get_setting("graph_fit_horizontal", "1") == "1"
        self._agenda_view.set_data(sessions, day_keys, hour_start, hour_end, fit_width)

        total_seconds = sum(s.get("duration_seconds", 0) for s in sessions)
        self._graph_subtitle.setText(
            f"Agenda · {self._range_label()} · {total_seconds / 3600:.1f}h · "
            f"{agenda_hour_label(hour_start)}–{agenda_hour_label(hour_end)}"
        )

        seen: dict[str, str] = {}
        for s in sessions:
            seen[s["subject_name"]] = s["color"]
        self._set_legend(seen)

    def _refresh_graphs_if_needed(self) -> None:
        if self.service.active_session or self._pages.currentIndex() == 2:
            self._reload_graphs()

    # ═══════════════════════════════════════════════════════════════════
    #  SETTINGS
    # ═══════════════════════════════════════════════════════════════════
    def _open_settings(self) -> None:
        dlg = SettingsDialog(self)
        if dlg.exec():
            settings = dlg.get_settings()
            self._fx = settings["theme_fx"]
            self._palette = settings["theme_palette"]
            db.set_setting("theme_fx", self._fx)
            db.set_setting("theme_palette", self._palette)
            self.service.set_day_start(settings["day_start_time"])
            self._apply_theme()
            self._reload()  # day-start affects subject totals + graphs

    def _open_graph_settings(self) -> None:
        dlg = GraphSettingsDialog(self)
        if dlg.exec():
            s = dlg.get_settings()
            self._graph_range_days = s["range_days"]
            self._graph_view_mode = s["view_mode"]
            self._graph_hour_start = s["hour_start"]
            self._graph_hour_end = s["hour_end"]
            self._graph_grouping = s["grouping"]
            self._graph_custom_range = s["custom_range"]
            self._reload_graphs()

    # ═══════════════════════════════════════════════════════════════════
    #  SUBJECT ACTIONS
    # ═══════════════════════════════════════════════════════════════════
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
        self._reload_subjects()
        self._reload_graphs()

    def _unarchive_subject(self, subject_id: int) -> None:
        self.service.unarchive_subject(subject_id)
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
        self.service.set_subject_order(ordered_ids)
        self._reload_subjects()

    def _manage_sessions(self, subject_id: int) -> None:
        ManageSessionsDialog(subject_id, self.service, self).exec()
        self._reload_subjects()
        self._reload_graphs()

    # ═══════════════════════════════════════════════════════════════════
    #  TASK ACTIONS
    # ═══════════════════════════════════════════════════════════════════
    def _new_todo_task(self) -> None:
        dlg = TodoTaskDialog(self)
        if dlg.exec():
            d = dlg.get_data()
            if not d["name"]:
                QMessageBox.warning(self, "Validation", "Task name cannot be empty.")
                return
            self.service.add_todo_task(d["name"], d["notes"], d["deadline"])
            self._reload_tasks()

    def _edit_todo_task(self, todo_task_id: int) -> None:
        todo_task = next((t for t in self.service.get_all_todo_tasks() if t.id == todo_task_id), None)
        if not todo_task:
            return
        dlg = TodoTaskDialog(self, todo_task)
        if dlg.exec():
            d = dlg.get_data()
            if not d["name"]:
                QMessageBox.warning(self, "Validation", "Task name cannot be empty.")
                return
            self.service.update_todo_task(todo_task_id, d["name"], d["notes"], d["deadline"])
            self._reload_tasks()

    def _delete_todo_task(self, todo_task_id: int) -> None:
        if QMessageBox.question(
            self,
            "Delete Task",
            "Delete this task permanently?",
            QMessageBox.Yes | QMessageBox.No,
        ) == QMessageBox.Yes:
            self.service.delete_todo_task(todo_task_id)
            self._reload_tasks()

    def _complete_todo_task(self, todo_task_id: int) -> None:
        def after_anim() -> None:
            self.service.complete_todo_task(todo_task_id)
            self._reload_tasks()

        self._todo_list.animate_remove(todo_task_id, on_finished=after_anim)

    def _on_todo_order_changed(self, ordered_ids: list[int]) -> None:
        self.service.set_todo_task_order(ordered_ids)
        self._reload_tasks()

    def _sort_todo_by_deadline(self) -> None:
        self.service.sort_todo_tasks_by_deadline()
        self._reload_tasks()

    # ═══════════════════════════════════════════════════════════════════
    #  TIMER
    # ═══════════════════════════════════════════════════════════════════
    def _start_tracking(self, subject_id: int) -> None:
        if self.service.start_subject(subject_id):
            self._reload_subjects()
            self._reload_graphs()

    def _stop_tracking(self) -> None:
        self.service.stop_active_subject()
        self._reload_subjects()
        self._reload_graphs()

    # ═══════════════════════════════════════════════════════════════════
    #  CLOSE
    # ═══════════════════════════════════════════════════════════════════
    def _on_close(self) -> None:
        if self.service.active_session:
            self.service.stop_active_subject()
