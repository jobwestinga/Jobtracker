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
    QEasingCurve,
    QEvent,
    QPointF,
    QRectF,
    QTimer,
    QVariantAnimation,
    Qt,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
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
from .accessibility import reduced_motion
from .controllers.keyboard import KeyboardController
from .controllers.sync import WindowSyncController
from .styles import build_stylesheet, _contrast_text
from .graphs_mixin import GraphsMixin
from .goals_mixin import GoalsMixin
from .subjects_mixin import SubjectsMixin
from .widgets.fx_background import FxBackgroundWidget
from .widgets.recovery_dialog import RecoveryDialog
from .widgets.dialog_utils import information
from .widgets.dialog_utils import open_dialog
from .widgets.settings_dialog import SettingsDialog
from .widgets.sync_status import SyncStatusWidget
from .widgets.weight_view import WeightView

logger = logging.getLogger("jobtracker")


class _SidebarNavButton(QPushButton):
    """Left-sidebar navigation button with a custom vector icon.

    Preserves ``button.text() == label`` for accessibility and test contracts.
    """

    def __init__(
        self,
        label: str,
        icon_kind: str,
        shortcut_hint: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(label, parent)
        self._icon_kind = icon_kind
        self._shortcut_hint = shortcut_hint
        self._tokens: dict = {}
        self.setMinimumHeight(38)

    def set_tokens(self, tokens: dict) -> None:
        self._tokens = tokens
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        t = self._tokens
        checked = self.isChecked()
        if checked:
            fg = QColor(_contrast_text(t.get("ACCENT", "#3B82F6")))
            hint_fg = QColor(fg)
            hint_fg.setAlpha(190)
        else:
            fg = QColor(t.get("TEXT_SECONDARY", "#94A3B8"))
            hint_fg = QColor(t.get("TEXT_DIMMED", "#526277"))

        cy = self.height() / 2.0
        cx = 19.0
        pen = QPen(fg, 1.55, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)

        if self._icon_kind == "goals":
            p.drawEllipse(QPointF(cx, cy), 6.2, 6.2)
            check = QPainterPath()
            check.moveTo(cx - 2.6, cy + 0.2)
            check.lineTo(cx - 0.6, cy + 2.2)
            check.lineTo(cx + 3.0, cy - 1.8)
            p.drawPath(check)
        elif self._icon_kind == "subjects":
            p.drawEllipse(QPointF(cx, cy), 6.2, 6.2)
            tri = QPolygonF(
                [
                    QPointF(cx - 1.6, cy - 2.6),
                    QPointF(cx - 1.6, cy + 2.6),
                    QPointF(cx + 2.8, cy),
                ]
            )
            p.setPen(Qt.NoPen)
            p.setBrush(fg)
            p.drawPolygon(tri)
        elif self._icon_kind == "graphs":
            p.setPen(Qt.NoPen)
            p.setBrush(fg)
            p.drawRoundedRect(QRectF(cx - 6.0, cy - 0.5, 2.8, 6.5), 1.0, 1.0)
            p.drawRoundedRect(QRectF(cx - 1.4, cy - 5.2, 2.8, 11.2), 1.0, 1.0)
            p.drawRoundedRect(QRectF(cx + 3.2, cy - 2.8, 2.8, 8.8), 1.0, 1.0)
        elif self._icon_kind == "weight":
            trend = QPainterPath()
            trend.moveTo(cx - 6.0, cy + 3.2)
            trend.lineTo(cx - 2.0, cy - 1.0)
            trend.lineTo(cx + 1.6, cy + 1.4)
            trend.lineTo(cx + 5.5, cy - 3.4)
            p.drawPath(trend)
            p.setPen(Qt.NoPen)
            p.setBrush(fg)
            p.drawEllipse(QPointF(cx + 5.5, cy - 3.4), 2.0, 2.0)

        if self._shortcut_hint:
            p.setPen(hint_fg)
            p.setFont(QFont("SF Pro Text", 9, QFont.DemiBold))
            hint_rect = QRectF(self.width() - 28, cy - 8, 22, 16)
            p.drawText(hint_rect, Qt.AlignRight | Qt.AlignVCenter, self._shortcut_hint)
        p.end()




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
        self._reduce_motion = self.service.get_setting("ui_reduce_motion", "0") == "1"
        self._presentation_active = app_instance.applicationState() == Qt.ApplicationActive
        self.setProperty("_jt_reduce_motion", self._reduce_motion)
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
        self._presentation_active = active
        self._sync_coordinator.foreground_changed(active)
        if active:
            # Coming back to the window is the moment the user is most likely to
            # care that the phone's changes have landed.
            self.sync_now()
            if hasattr(self, "_weight_view"):
                self._weight_view.refresh()
        if hasattr(self, "_fx_bg"):
            self._fx_bg.set_animating(active and not self._reduce_motion)
        if hasattr(self, "_graph_live_timer"):
            if active:
                if not self._graph_live_timer.isActive():
                    self._graph_live_timer.start()
            else:
                self._graph_live_timer.stop()

    def changeEvent(self, event) -> None:  # noqa: N802
        if event.type() == QEvent.WindowStateChange and hasattr(self, "_fx_bg"):
            minimized = bool(self.windowState() & Qt.WindowMinimized)
            self._fx_bg.set_animating(not minimized and self._presentation_active and not self._reduce_motion)
        super().changeEvent(event)

    def _install_shortcuts(self) -> None:
        return self._keyboard._install_shortcuts()

    def _shortcut_focus_allows_navigation(self) -> bool:
        return self._keyboard._shortcut_focus_allows_navigation()

    def _navigate_pages(self, delta: int) -> None:
        return self._keyboard._navigate_pages(delta)

    def _navigate_to_page(self, index: int) -> None:
        return self._keyboard._navigate_to_page(index)

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

        root = QHBoxLayout(content)
        root.setContentsMargins(12, 12, 14, 12)
        root.setSpacing(12)

        self._sidebar = self._build_left_sidebar(content)
        root.addWidget(self._sidebar, 0)

        self._pages = QStackedWidget()
        root.addWidget(self._pages, 1)

        self._page_fade_overlay = QLabel(self._pages)
        self._page_fade_overlay.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._page_fade_overlay.hide()
        self._page_fade_effect = QGraphicsOpacityEffect(self._page_fade_overlay)
        self._page_fade_overlay.setGraphicsEffect(self._page_fade_effect)
        self._page_fade_anim = QVariantAnimation(self)
        self._page_fade_anim.setDuration(130)
        self._page_fade_anim.setStartValue(0.32)
        self._page_fade_anim.setEndValue(0.0)
        self._page_fade_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._page_fade_anim.valueChanged.connect(
            lambda val: self._page_fade_effect.setOpacity(float(val))
        )
        self._page_fade_anim.finished.connect(self._page_fade_overlay.hide)

        self._build_tasks_page()
        self._build_subjects_page()
        self._build_graphs_page()
        self._weight_view = WeightView(self.service)
        self._weight_view._fullscreen_btn.toggled.connect(
            lambda expanded: self._sidebar.setVisible(not expanded)
        )
        self._weight_view.changed.connect(self.sync_now)
        self._pages.addWidget(self._weight_view)

        self._switch_page(1)

    def _build_left_sidebar(self, parent: QWidget) -> QFrame:
        bar = QFrame(parent)
        bar.setObjectName("leftSidebar")
        bar.setFixedWidth(142)

        nav = QVBoxLayout(bar)
        nav.setContentsMargins(9, 12, 9, 10)
        nav.setSpacing(5)

        brand_row = QHBoxLayout()
        brand_row.setContentsMargins(4, 0, 0, 2)
        brand_row.setSpacing(4)
        brand_lbl = QLabel("JobTracker")
        brand_lbl.setObjectName("sidebarBrand")
        brand_row.addWidget(brand_lbl)
        brand_row.addStretch(1)

        gear_btn = QPushButton("⚙")
        gear_btn.setObjectName("gearBtn")
        gear_btn.setFixedSize(30, 30)
        gear_btn.setCursor(Qt.PointingHandCursor)
        gear_btn.setToolTip("Settings")
        gear_btn.setAccessibleName("Settings")
        gear_btn.clicked.connect(self._open_settings)
        brand_row.addWidget(gear_btn)
        nav.addLayout(brand_row)

        self._sync_status = SyncStatusWidget(bar)
        self._sync_status.action_requested.connect(self._sync_coordinator.status_action)
        nav.addWidget(self._sync_status)

        nav.addSpacing(4)

        specs = [
            ("Goals", "goals", ""),
            ("Subjects", "subjects", ""),
            ("Graphs", "graphs", ""),
            ("Weight", "weight", ""),
        ]
        self._nav_buttons: list[QPushButton] = []

        for idx, (label, icon_kind, hint) in enumerate(specs):
            btn = _SidebarNavButton(label, icon_kind, hint, bar)
            btn.setObjectName("navBtn")
            btn.setCheckable(True)
            btn.setFocusPolicy(Qt.StrongFocus)
            btn.setAccessibleName(f"{label} tab")
            btn.setToolTip(f"{label} (Left/Right arrows)")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(
                lambda _checked=False, i=idx: self._queue_ui_action(
                    self._switch_page, i
                )
            )
            nav.addWidget(btn)
            self._nav_buttons.append(btn)

        nav.addStretch(1)

        self._active_session_indicator = QPushButton(bar)
        self._active_session_indicator.setMinimumHeight(46)
        self._active_session_indicator.setCursor(Qt.PointingHandCursor)
        self._active_session_indicator.setToolTip(
            "Return to Subjects. This does not stop tracking."
        )
        self._active_session_indicator.setAccessibleName("Return to the active timer")
        self._active_session_indicator.clicked.connect(
            lambda: self._queue_ui_action(self._switch_page, 1)
        )
        self._active_session_indicator.hide()
        nav.addWidget(self._active_session_indicator)

        return bar

    def _switch_page(self, index: int) -> None:
        prev_index = self._pages.currentIndex()
        if prev_index == 3 and index != 3:
            self._weight_view._fullscreen_btn.setChecked(False)
        can_animate = (
            prev_index != index
            and self.isVisible()
            and not reduced_motion(self)
            and self._pages.width() > 0
            and self._pages.height() > 0
        )
        if can_animate:
            current_widget = self._pages.currentWidget()
            if current_widget is not None:
                snap = current_widget.grab()
                if not snap.isNull():
                    self._page_fade_anim.stop()
                    self._page_fade_overlay.setPixmap(snap)
                    self._page_fade_overlay.setGeometry(self._pages.rect())
                    self._page_fade_effect.setOpacity(0.32)
                    self._page_fade_overlay.show()
                    self._page_fade_overlay.raise_()
                    self._page_fade_anim.start()
        self._pages.setCurrentIndex(index)
        for i, btn in enumerate(self._nav_buttons):
            btn.setChecked(i == index)
        if any(btn.hasFocus() for btn in self._nav_buttons):
            self._nav_buttons[index].setFocus(Qt.ShortcutFocusReason)
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
            f"QPushButton {{ background: {self._tokens.get('BG_PRIMARY', self._tokens['BG_SECONDARY'])};"
            f" border: 1.5px solid {color};"
            f" border-radius: 9px; color: {self._tokens['TEXT_PRIMARY']};"
            " font-size: 11px; font-weight: 700; text-align: left;"
            " padding: 6px 9px; }"
            f"QPushButton:hover {{ background: {self._tokens.get('BG_TERTIARY', self._tokens['BG_SECONDARY'])}; }}"
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
        name = self._active_session_indicator.fontMetrics().elidedText(
            subject.name, Qt.ElideRight,
            max(1, self._active_session_indicator.width() - 34),
        )
        self._active_session_indicator.setText(
            f"● {name}\n"
            f"{hours:02d}:{minutes:02d}:{seconds:02d}  ›"
        )
        self._active_session_indicator.setToolTip(
            f"Tracking: {subject.name} ({hours:02d}:{minutes:02d}:{seconds:02d}) — Return to Subjects"
        )

    # ═══════════════════════════════════════════════════════════════════
    #  THEME / RELOAD
    # ═══════════════════════════════════════════════════════════════════
    def _apply_theme(self) -> None:
        self._reduce_motion = self.service.get_setting("ui_reduce_motion", "0") == "1"
        self.setProperty("_jt_reduce_motion", self._reduce_motion)
        self._tokens = get_tokens(self._fx, self._palette)
        self.app_instance.setStyleSheet(build_stylesheet(self._tokens))
        self._timer.apply_tokens(self._tokens)
        self._graph_view.set_tokens(self._tokens)
        self._agenda_view.set_tokens(self._tokens)
        self._weight_view.set_tokens(self._tokens)
        self._fx_bg.apply_theme(self._tokens, self._fx)
        self._fx_bg.set_animating(self._presentation_active and not self._reduce_motion)
        self._sync_status.apply_tokens(self._tokens)
        for btn in getattr(self, "_nav_buttons", []):
            if hasattr(btn, "set_tokens"):
                btn.set_tokens(self._tokens)
        self._sync_active_session_indicator()

    def _reload(self) -> None:
        self._reload_subjects()
        self._reload_tasks()
        self._reload_graphs()
        self._weight_view.refresh()
        self._sync_coordinator.refresh_status()

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
        self._reduce_motion = settings.get("ui_reduce_motion", self._reduce_motion)
        self.service.set_setting("ui_reduce_motion", "1" if self._reduce_motion else "0")
        self.setProperty("_jt_reduce_motion", self._reduce_motion)
        self._schedule_recurring_boundary()
        self._apply_theme()
        self._reload()  # day-start affects subject totals + graphs
        self._sync_coordinator.refresh_status(force=True, configuration=True)

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
        self._sync_coordinator.close()
        super().closeEvent(event)
