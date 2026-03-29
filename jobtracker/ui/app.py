"""
Main application window with 3 environments:
- Subjects
- Tasks
- Graphs
"""

from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import QTimer, Qt, QRect
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
from ..services.tracker_service import TrackerService
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

        self.app_instance.aboutToQuit.connect(self._on_close)

        self._build_ui()
        self._apply_theme()
        self._reload()

        # Keep graphs up to date while tracking, without requiring manual refresh.
        self._graph_live_timer = QTimer(self)
        self._graph_live_timer.setInterval(2000)
        self._graph_live_timer.timeout.connect(self._refresh_graphs_if_needed)
        self._graph_live_timer.start()

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

        add_btn = QPushButton("+ Subject")
        add_btn.setObjectName("primaryBtn")
        add_btn.setMinimumHeight(34)
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.clicked.connect(self._new_subject)
        header.addWidget(add_btn)

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

        subjects = self.service.get_all_subjects()
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
            )
            card.start_requested.connect(self._start_tracking)
            card.edit_requested.connect(self._edit_subject)
            card.delete_requested.connect(self._delete_subject)
            card.manage_sessions_requested.connect(self._manage_sessions)
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

    def _reload_bar_chart(self) -> None:
        self._graph_stack.setCurrentIndex(0)
        data = self.service.get_daily_subject_breakdown(self._graph_range_days)
        self._graph_view.set_data(data)

        total_seconds = sum(day["total_seconds"] for day in data)
        range_label = f"{self._graph_range_days} days" if self._graph_range_days else "All Time"
        self._graph_subtitle.setText(
            f"{range_label} total: {total_seconds / 3600:.1f}h"
        )

        seen: dict[str, str] = {}
        for day in data:
            for seg in day["segments"]:
                seen[seg["subject_name"]] = seg["color"]
        if seen:
            parts = [
                f"<span style='color:{color};'>■</span> {name}"
                for name, color in seen.items()
            ]
            self._graph_legend.setText("&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;".join(parts))
        else:
            self._graph_legend.setText("No tracked sessions in the displayed range.")

    def _reload_agenda(self) -> None:
        self._graph_stack.setCurrentIndex(1)

        today = date.today()
        if self._graph_range_days is None:
            earliest_iso = db.get_earliest_session_date()
            if earliest_iso:
                try:
                    start_day = __import__("datetime").datetime.fromisoformat(earliest_iso).date()
                except ValueError:
                    start_day = today
            else:
                start_day = today
        else:
            start_day = today - timedelta(days=max(1, self._graph_range_days) - 1)

        day_keys = [
            (start_day + timedelta(days=i)).isoformat()
            for i in range((today - start_day).days + 1)
        ]

        sessions = self.service.get_sessions_in_range(start_day, today)
        self._agenda_view.set_data(
            sessions, day_keys, self._graph_hour_start, self._graph_hour_end
        )

        total_seconds = sum(s.get("duration_seconds", 0) for s in sessions)
        range_label = f"{self._graph_range_days} days" if self._graph_range_days else "All Time"
        self._graph_subtitle.setText(
            f"Agenda · {range_label} · {total_seconds / 3600:.1f}h · "
            f"{self._graph_hour_start:02d}:00–{self._graph_hour_end:02d}:00"
        )

        seen: dict[str, str] = {}
        for s in sessions:
            seen[s["subject_name"]] = s["color"]
        if seen:
            parts = [
                f"<span style='color:{color};'>■</span> {name}"
                for name, color in seen.items()
            ]
            self._graph_legend.setText("&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;".join(parts))
        else:
            self._graph_legend.setText("No tracked sessions in the displayed range.")

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
            self._apply_theme()
            self._reload()

    def _open_graph_settings(self) -> None:
        dlg = GraphSettingsDialog(self)
        if dlg.exec():
            s = dlg.get_settings()
            self._graph_range_days = s["range_days"]
            self._graph_view_mode = s["view_mode"]
            self._graph_hour_start = s["hour_start"]
            self._graph_hour_end = s["hour_end"]
            self._reload_graphs()

    # ═══════════════════════════════════════════════════════════════════
    #  SUBJECT ACTIONS
    # ═══════════════════════════════════════════════════════════════════
    def _new_subject(self) -> None:
        dlg = SubjectDialog(self)
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

    def _edit_subject(self, subject_id: int) -> None:
        subject = next((s for s in self.service.get_all_subjects() if s.id == subject_id), None)
        if not subject:
            return

        dlg = SubjectDialog(self)
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
        if QMessageBox.question(
            self,
            "Delete Subject",
            "This will permanently delete this subject and all its sessions.\nContinue?",
            QMessageBox.Yes | QMessageBox.No,
        ) == QMessageBox.Yes:
            self.service.delete_subject(subject_id)
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
