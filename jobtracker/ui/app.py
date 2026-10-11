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

from PySide6.QtCore import (
    QEvent,
    QTimer,
    QVariantAnimation,
    Qt,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QMainWindow,
    QPushButton,
    QStackedLayout,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core import timeutils
from ..core.auto_backup import write_auto_backup
from ..core.config import BACKUPS_DIR
from ..core.themes import get_tokens
from ..services.tracker_service import TrackerService
from ..sync import settings as sync_settings
from ..sync.service import SyncedTrackerService
from .controllers.keyboard import KeyboardController
from .controllers.sync import WindowSyncController
from .styles import build_stylesheet
from .graphs_mixin import GraphsMixin
from .goals_mixin import GoalsMixin
from .subjects_mixin import SubjectsMixin
from .widgets.fx_background import FxBackgroundWidget
from .widgets.recovery_dialog import RecoveryDialog
from .widgets.dialog_utils import information
from .widgets.dialog_utils import open_dialog
from .widgets.settings_dialog import SettingsDialog
from .widgets.weight_view import WeightView

logger = logging.getLogger("jobtracker")




class MainWindow(SubjectsMixin, GoalsMixin, GraphsMixin, QMainWindow):
    def __init__(self, app_instance) -> None:
        super().__init__()
        self.app_instance = app_instance
        self._keyboard = KeyboardController(self)
        self._sync_coordinator = WindowSyncController(self)
        # A synced mirror must queue recovery writes too. Inspect settings
        # without recovery first, then recover through the appropriate service.
        self.service = TrackerService(recover=False)
        self._sync_on = sync_settings.is_enabled(self.service)
        if self._sync_on:
            self.service = SyncedTrackerService(
                self.service.db, device_id=self.service.device_id, recover=True
            )
        else:
            self.service.recover_open_sessions()
        self._sync_controller = None
        self.setWindowTitle("JobTracker")
        self.setMinimumSize(560, 760)
        self.resize(680, 920)

        self._fx = self.service.get_setting("theme_fx", "Glow")
        self._palette = self.service.get_setting("theme_palette", "Ocean")
        self._tokens = get_tokens(self._fx, self._palette)

        # Graph settings (persisted). Bucket size follows the range unless the
        # user has explicitly overridden it.
        self._graph_range_preset: str = self._load_graph_range_preset()
        self._graph_grouping: str = self._load_graph_grouping()
        self._graph_view_mode: str = self.service.get_setting("graph_view_mode", "bar")
        if self._graph_view_mode not in ("bar", "agenda"):
            # The heatmap was removed; anyone whose last view was that one gets
            # bars rather than a blank page.
            self._graph_view_mode = "bar"
            self.service.set_setting("graph_view_mode", "bar")
        self._graph_hour_start = self._load_graph_hour("graph_hour_start", 6)
        self._graph_hour_end = self._load_graph_hour("graph_hour_end", 23)
        if self._graph_hour_end <= self._graph_hour_start:
            self._graph_hour_start, self._graph_hour_end = 6, 23
        self._graph_custom_range: tuple[date, date] | None = self._load_custom_range()

        self._showing_archived: bool = False
        self._showing_completed_goals: bool = False
        self._undo_callback = None
        self._shortcut_feedback_anims: list[QVariantAnimation] = []

        # Pause expensive background animation when the app is not in front.
        self.app_instance.applicationStateChanged.connect(self._on_app_state_changed)

        self._build_ui()
        self._active_indicator_timer = QTimer(self)
        self._active_indicator_timer.setInterval(500)
        self._active_indicator_timer.timeout.connect(
            self._update_active_session_indicator
        )
        self._install_arrow_direction_filter()
        self._install_shortcuts()
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

        self._setup_sync()

    # ── sync ────────────────────────────────────────────────────────────
    def _load_graph_hour(self, key: str, default: int) -> int:
        try:
            return min(27, max(0, int(self.service.get_setting(key, str(default)))))
        except (TypeError, ValueError):
            return default

    def _setup_sync(self) -> None:
        return self._sync_coordinator._setup_sync()

    def sync_now(self) -> bool:
        return self._sync_coordinator.sync_now()

    def _on_sync_finished(self, result) -> None:
        return self._sync_coordinator._on_sync_finished(result)

    def _install_arrow_direction_filter(self) -> None:
        return self._keyboard._install_arrow_direction_filter()

    # ── state / animation wiring ────────────────────────────────────────
    def _maybe_prompt_recovery(self) -> None:
        info = self.service.get_active_recovery_info()
        if not info:
            return
        self._recovery_session_id = info.session_id
        self._recovery_session_uid = self.service.active_session.uid
        dlg = RecoveryDialog(info, self)
        open_dialog(dlg, self._finish_recovery)

    def _finish_recovery(self, result: int, dialog: QDialog) -> None:
        active = self.service.active_session
        if (
            result == QDialog.Accepted and active is not None
            and active.uid == getattr(self, "_recovery_session_uid", None)
        ):
            self.service.resolve_recovery(dialog.chosen_end(), session_id=active.id)
        self._recovery_session_uid = None
        # If dismissed, the session stays active (non-destructive) and keeps ticking.
        self._reload_subjects()
        self._reload_graphs()

    def _on_app_state_changed(self, state) -> None:
        active = state == Qt.ApplicationActive
        if active:
            # Coming back to the window is the moment the user is most likely to
            # care that the phone's changes have landed.
            self.sync_now()
            if hasattr(self, "_weight_view"):
                self._weight_view.refresh()
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

    def _install_shortcuts(self) -> None:
        return self._keyboard._install_shortcuts()

    def _shortcut_focus_allows_navigation(self) -> bool:
        return self._keyboard._shortcut_focus_allows_navigation()

    def _navigate_pages(self, delta: int) -> None:
        return self._keyboard._navigate_pages(delta)

    def _handle_number_shortcut(self, number: int) -> None:
        return self._keyboard._handle_number_shortcut(number)

    def _open_goal_after_shortcut_feedback(self, goal_id: int) -> None:
        return self._keyboard._open_goal_after_shortcut_feedback(goal_id)

    def _handle_escape_shortcut(self) -> None:
        return self._keyboard._handle_escape_shortcut()

    def _handle_graph_range_shortcut(self, preset: str) -> None:
        return self._keyboard._handle_graph_range_shortcut(preset)

    def _pulse_widget(self, widget: QWidget, duration_ms: int = 180) -> None:
        return self._keyboard._pulse_widget(widget, duration_ms)

    def _register_undo(self, callback) -> None:
        return self._keyboard._register_undo(callback)

    def _queue_ui_action(self, callback, *args) -> None:
        return self._keyboard._queue_ui_action(callback, *args)

    def _perform_undo(self, force: bool = False) -> None:
        return self._keyboard._perform_undo(force)

    def _heartbeat(self) -> None:
        try:
            # Preserve the last known activity shown in an unresolved recovery
            # prompt; a minute tick must not silently validate its whole gap.
            if not getattr(self, "_recovery_session_uid", None):
                self.service.heartbeat_active_session()
        except Exception:
            logger.exception("Heartbeat failed")
        # Card totals include the live session, so nudge them along roughly
        # once a minute while the subjects page is visible.
        if (
            self.service.active_session is not None
            and self._pages.currentIndex() == 1
            and not self._showing_archived
        ):
            self._update_tracking_state_inplace()

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
        self._weight_view = WeightView(self.service)
        self._weight_view.changed.connect(self.sync_now)
        self._pages.addWidget(self._weight_view)

        self._active_session_indicator = QPushButton()
        self._active_session_indicator.setMinimumHeight(38)
        self._active_session_indicator.setCursor(Qt.PointingHandCursor)
        self._active_session_indicator.setToolTip(
            "Return to Subjects. This does not stop tracking."
        )
        self._active_session_indicator.clicked.connect(
            lambda: self._queue_ui_action(self._switch_page, 1)
        )
        self._active_session_indicator.hide()
        root.addWidget(self._active_session_indicator)

        root.addWidget(self._build_bottom_nav())
        self._switch_page(1)

    def _build_bottom_nav(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("bottomNav")
        bar.setFixedHeight(54)

        nav = QHBoxLayout(bar)
        nav.setContentsMargins(0, 0, 0, 0)
        nav.setSpacing(0)

        labels = ["Goals", "Subjects", "Graphs", "Weight"]
        self._nav_buttons: list[QPushButton] = []

        for idx, label in enumerate(labels):
            btn = QPushButton(label)
            btn.setObjectName("navBtn")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(
                lambda _checked=False, i=idx: self._queue_ui_action(
                    self._switch_page, i
                )
            )
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
        elif index == 3:
            self._weight_view.refresh()
        self._sync_active_session_indicator()

    def _sync_active_session_indicator(self) -> None:
        if not hasattr(self, "_active_session_indicator"):
            return
        show = bool(
            self.service.active_session
            and self.service.active_subject
            and self._pages.currentIndex() != 1
        )
        self._active_session_indicator.setVisible(show)
        if not show:
            if hasattr(self, "_active_indicator_timer"):
                self._active_indicator_timer.stop()
            return

        subject = self.service.active_subject
        color = subject.color
        self._active_session_indicator.setStyleSheet(
            f"QPushButton {{ background: {self._tokens.get('CARD_BG', self._tokens['BG_SECONDARY'])};"
            f" border: 1.4px solid {color}; border-left: 5px solid {color};"
            f" border-radius: 10px; color: {self._tokens['TEXT_PRIMARY']};"
            " font-size: 12px; font-weight: 700; text-align: left;"
            " padding: 7px 12px; }"
            f"QPushButton:hover {{ background: {self._tokens.get('CARD_HOVER_BG', self._tokens['BG_TERTIARY'])}; }}"
        )
        self._update_active_session_indicator()
        if (
            hasattr(self, "_active_indicator_timer")
            and not self._active_indicator_timer.isActive()
        ):
            self._active_indicator_timer.start()

    def _update_active_session_indicator(self) -> None:
        session = self.service.active_session
        subject = self.service.active_subject
        if session is None or subject is None:
            self._sync_active_session_indicator()
            return
        started = timeutils.parse_iso(session.start_time)
        elapsed = timeutils.duration_seconds(started, datetime.now())
        hours, remainder = divmod(elapsed, 3600)
        minutes, seconds = divmod(remainder, 60)
        self._active_session_indicator.setText(
            f"●  Tracking: {subject.name}  ·  "
            f"{hours:02d}:{minutes:02d}:{seconds:02d}   ›"
        )

    # ═══════════════════════════════════════════════════════════════════
    #  THEME / RELOAD
    # ═══════════════════════════════════════════════════════════════════
    def _apply_theme(self) -> None:
        self._tokens = get_tokens(self._fx, self._palette)
        self.app_instance.setStyleSheet(build_stylesheet(self._tokens))
        self._timer.apply_tokens(self._tokens)
        self._graph_view.set_tokens(self._tokens)
        self._agenda_view.set_tokens(self._tokens)
        self._weight_view.set_tokens(self._tokens)
        self._fx_bg.apply_theme(self._tokens, self._fx)
        self._sync_active_session_indicator()

    def _reload(self) -> None:
        self._reload_subjects()
        self._reload_tasks()
        self._reload_graphs()
        self._weight_view.refresh()

    # ═══════════════════════════════════════════════════════════════════
    #  GLOBAL SETTINGS
    # ═══════════════════════════════════════════════════════════════════
    def _open_settings(self) -> None:
        dlg = SettingsDialog(self, service=self.service)
        open_dialog(dlg, self._finish_settings)

    def _finish_settings(self, result: int, dialog: QDialog) -> None:
        if result != QDialog.Accepted:
            return
        settings = dialog.get_settings()
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
    def closeEvent(self, event) -> None:  # noqa: N802
        # Only block quitting while a session is *genuinely* tracking (its
        # heartbeat is recent). A session left open by a crash/force-kill is
        # stale (a large heartbeat gap) — that is "no task actively running",
        # so allow the quit. The session stays open and is picked up by
        # recovery on the next launch; it is never silently dropped.
        genuinely_tracking = (
            self.service.active_session is not None
            and self.service.get_active_recovery_info() is None
        )
        if genuinely_tracking:
            event.ignore()
            self.showNormal()
            self.show()
            self.raise_()
            self.activateWindow()
            self._switch_page(1)
            self._timer.pulse_stop_button()
            information(
                self,
                "Session Still Active",
                "JobTracker cannot quit while a session is active.\n\n"
                "Use the red Stop Tracking button first.",
            )
            return

        # Quit is going through. Push whatever is queued first, but never let
        # sync delay the quit for long: the outbox is durable, so anything that
        # does not make it now simply goes at next launch.
        try:
            if self.sync_now() and self._sync_controller is not None:
                self._sync_controller.wait_for_idle(3000)
        except Exception:
            logger.exception("Sync on quit failed")

        # Leave a rotating JSON safety copy behind.
        # Never let a backup problem block quitting.
        try:
            write_auto_backup(self.service.export_data(), BACKUPS_DIR)
        except Exception:
            logger.exception("Auto-backup on quit failed")
        super().closeEvent(event)
