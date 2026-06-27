"""
Main application window. The per-area logic lives in mixins:
- SubjectsMixin (subjects_mixin.py)
- GoalsMixin    (goals_mixin.py)
- GraphsMixin   (graphs_mixin.py)

This module keeps only the shell: construction, theme, navigation, the global
settings dialog, animation/state wiring, recovery prompt, and close handling.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from PySide6.QtCore import QEvent, QTimer, Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QMainWindow,
    QPushButton,
    QStackedLayout,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.themes import get_tokens
from ..services.tracker_service import TrackerService
from .styles import build_stylesheet
from .graphs_mixin import GraphsMixin
from .goals_mixin import GoalsMixin
from .subjects_mixin import SubjectsMixin
from .widgets.fx_background import FxBackgroundWidget
from .widgets.recovery_dialog import RecoveryDialog
from .widgets.settings_dialog import SettingsDialog

logger = logging.getLogger("jobtracker")


class MainWindow(SubjectsMixin, GoalsMixin, GraphsMixin, QMainWindow):
    def __init__(self, app_instance) -> None:
        super().__init__()
        self.app_instance = app_instance
        self.service = TrackerService()
        self.setWindowTitle("JobTracker")
        self.setMinimumSize(560, 760)
        self.resize(680, 920)

        # The old graph grouping preference is obsolete: grouping is derived
        # from the selected date range. Remove the stale value once at launch.
        self.service.delete_setting("graph_grouping")

        self._fx = self.service.get_setting("theme_fx", "Glow")
        self._palette = self.service.get_setting("theme_palette", "Ocean")
        self._tokens = get_tokens(self._fx, self._palette)

        # Graph settings (persisted). Bucket size is derived from the range now.
        self._graph_range_preset: str = self._load_graph_range_preset()
        self._graph_view_mode: str = self.service.get_setting("graph_view_mode", "bar")
        self._graph_hour_start: int = int(
            self.service.get_setting("graph_hour_start", "6")
        )
        self._graph_hour_end: int = int(
            self.service.get_setting("graph_hour_end", "23")
        )
        self._graph_custom_range: tuple[date, date] | None = self._load_custom_range()

        self._showing_archived: bool = False
        self._showing_completed_goals: bool = False

        self.app_instance.aboutToQuit.connect(self._on_close)
        # Pause expensive background animation when the app is not in front.
        self.app_instance.applicationStateChanged.connect(self._on_app_state_changed)

        self._build_ui()
        self._apply_theme()
        self._generate_due_goals(reload=False)
        self._reload()

        # Keep graphs up to date while tracking, without requiring manual refresh.
        self._graph_live_timer = QTimer(self)
        self._graph_live_timer.setInterval(2000)
        self._graph_live_timer.timeout.connect(self._refresh_graphs_if_needed)
        self._graph_live_timer.start()

        # Heartbeat: record that the active session is still legitimately running
        # roughly once a minute. One cheap UPDATE, no history rows.
        self._heartbeat_timer = QTimer(self)
        self._heartbeat_timer.setInterval(60_000)
        self._heartbeat_timer.timeout.connect(self._heartbeat)
        self._heartbeat_timer.start()

        # Generate recurring goals exactly when the configured logical day rolls
        # over (03:00 by default), then schedule the next boundary.
        self._recurring_timer = QTimer(self)
        self._recurring_timer.setSingleShot(True)
        self._recurring_timer.timeout.connect(self._on_recurring_boundary)
        self._schedule_recurring_boundary()

        # After the window is up, offer recovery for any unfinished session.
        QTimer.singleShot(0, self._maybe_prompt_recovery)

    # ── state / animation wiring ────────────────────────────────────────
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

    def _schedule_recurring_boundary(self) -> None:
        now = datetime.now()
        day_start = self.service.get_day_start()
        boundary = datetime.combine(now.date(), day_start)
        if boundary <= now:
            boundary += timedelta(days=1)
        delay_ms = max(1000, int((boundary - now).total_seconds() * 1000))
        self._recurring_timer.start(delay_ms)

    def _on_recurring_boundary(self) -> None:
        self._generate_due_goals()
        self._schedule_recurring_boundary()

    # ═══════════════════════════════════════════════════════════════════
    #  UI shell
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

        self._build_tasks_page()
        self._build_subjects_page()
        self._build_graphs_page()

        root.addWidget(self._build_bottom_nav())
        self._switch_page(1)

    def _build_bottom_nav(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("bottomNav")
        bar.setFixedHeight(54)

        nav = QHBoxLayout(bar)
        nav.setContentsMargins(0, 0, 0, 0)
        nav.setSpacing(0)

        labels = ["Goals", "Subjects", "Graphs"]
        self._nav_buttons: list[QPushButton] = []

        for idx, label in enumerate(labels):
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
        if index == 0:
            # Generate due recurring goals when entering the Goals tab (plus on
            # launch). Cheaper than the old every-minute check; idempotent.
            self._generate_due_goals()
        elif index == 2:
            self._reload_graphs()

    # ═══════════════════════════════════════════════════════════════════
    #  THEME / RELOAD
    # ═══════════════════════════════════════════════════════════════════
    def _apply_theme(self) -> None:
        self._tokens = get_tokens(self._fx, self._palette)
        self.app_instance.setStyleSheet(build_stylesheet(self._tokens))
        self._timer.apply_tokens(self._tokens)
        self._graph_view.set_tokens(self._tokens)
        self._agenda_view.set_tokens(self._tokens)
        self._heatmap_view.set_tokens(self._tokens)
        self._fx_bg.apply_theme(self._tokens, self._fx)

    def _reload(self) -> None:
        self._reload_subjects()
        self._reload_tasks()
        self._reload_graphs()

    # ═══════════════════════════════════════════════════════════════════
    #  GLOBAL SETTINGS
    # ═══════════════════════════════════════════════════════════════════
    def _open_settings(self) -> None:
        dlg = SettingsDialog(self, service=self.service)
        if dlg.exec():
            settings = dlg.get_settings()
            self._fx = settings["theme_fx"]
            self._palette = settings["theme_palette"]
            self.service.set_setting("theme_fx", self._fx)
            self.service.set_setting("theme_palette", self._palette)
            self.service.set_day_start(settings["day_start_time"])
            self._schedule_recurring_boundary()
            self._apply_theme()
            self._reload()  # day-start affects subject totals + graphs

    # ═══════════════════════════════════════════════════════════════════
    #  CLOSE
    # ═══════════════════════════════════════════════════════════════════
    def _on_close(self) -> None:
        if self.service.active_session:
            self.service.stop_active_subject()
