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
import weakref
from datetime import date, datetime, timedelta

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QTimer,
    QVariantAnimation,
    Qt,
)
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLineEdit,
    QListView,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QStackedLayout,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..core import timeutils
from ..core.themes import get_tokens
from ..services.tracker_service import TrackerService
from .styles import build_stylesheet
from .graphs_mixin import GraphsMixin
from .goals_mixin import GoalsMixin
from .subjects_mixin import SubjectsMixin
from .widgets.fx_background import FxBackgroundWidget
from .widgets.recovery_dialog import RecoveryDialog
from .widgets.dialog_utils import information
from .widgets.dialog_utils import open_dialog
from .widgets.settings_dialog import SettingsDialog

logger = logging.getLogger("jobtracker")


class _LatestArrowDirectionFilter(QObject):
    """Prevent stale key-repeat events from delaying Up/Down reversal.

    macOS can leave repeat events from one held arrow in Qt's input queue after
    the opposite arrow is pressed. Native menus then appear to keep travelling
    in the old direction. The latest physical press owns repeat until another
    physical press changes direction.
    """

    _DIRECTION_KEYS = {Qt.Key_Up, Qt.Key_Down}

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._held: set[int] = set()
        self._preferred: int | None = None
        self._view_states = weakref.WeakKeyDictionary()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        event_type = event.type()
        if event_type == QEvent.ApplicationDeactivate:
            self._held.clear()
            self._view_states.clear()
            return False
        view = self._item_view_for(watched)
        if view is not None and event_type in (
            QEvent.FocusIn,
            QEvent.MouseButtonPress,
            QEvent.Wheel,
        ):
            self._view_states.pop(view, None)
        if event_type not in (QEvent.KeyPress, QEvent.KeyRelease):
            return False

        key = event.key()
        if key not in self._DIRECTION_KEYS:
            if view is not None and event_type == QEvent.KeyPress:
                self._view_states.pop(view, None)
            return False

        if event_type == QEvent.KeyPress:
            if event.isAutoRepeat():
                if key not in self._held or key != self._preferred:
                    return True
            else:
                self._held.add(key)
                self._preferred = key
            if view is not None:
                if view.property("_jt_direct_arrow_scroll"):
                    view.scroll_one_card(key)
                    return True
                self._follow_view_scroll_after_key(view, key)
            return False

        if not event.isAutoRepeat():
            self._held.discard(key)
        return False

    @staticmethod
    def _item_view_for(watched) -> QAbstractItemView | None:
        current = watched if isinstance(watched, QWidget) else None
        while current is not None:
            if isinstance(current, QAbstractItemView):
                return current
            current = current.parentWidget()
        return None

    def _follow_view_scroll_after_key(
        self, view: QAbstractItemView, key: int
    ) -> None:
        try:
            before = view.verticalScrollBar().value()
            view_ref = weakref.ref(view)
        except RuntimeError:
            return

        def adjust() -> None:
            current_view = view_ref()
            if current_view is None:
                return
            try:
                bar = current_view.verticalScrollBar()
                after = bar.value()
                state = self._view_states.setdefault(
                    current_view, {"following": False}
                )
                if after != before:
                    state["following"] = True
                    return
                if not state["following"] or bar.maximum() <= bar.minimum():
                    return

                if (
                    current_view.verticalScrollMode()
                    == QAbstractItemView.ScrollPerPixel
                ):
                    rect = current_view.visualRect(
                        current_view.currentIndex()
                    )
                    step = max(1, rect.height())
                    if isinstance(current_view, QListView):
                        step += current_view.spacing()
                else:
                    step = 1

                direction = -1 if key == Qt.Key_Up else 1
                bar.setValue(before + direction * step)
                if bar.value() == before:
                    state["following"] = False
            except RuntimeError:
                return

        # Run after the native view has moved its current selection.
        QTimer.singleShot(0, adjust)


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

    def _install_arrow_direction_filter(self) -> None:
        arrow_filter = getattr(
            self.app_instance, "_jobtracker_arrow_direction_filter", None
        )
        if arrow_filter is None:
            arrow_filter = _LatestArrowDirectionFilter(self.app_instance)
            self.app_instance.installEventFilter(arrow_filter)
            self.app_instance._jobtracker_arrow_direction_filter = arrow_filter

    # ── state / animation wiring ────────────────────────────────────────
    def _maybe_prompt_recovery(self) -> None:
        info = self.service.get_active_recovery_info()
        if not info:
            return
        dlg = RecoveryDialog(info, self)
        open_dialog(dlg, self._finish_recovery)

    def _finish_recovery(self, result: int, dialog: QDialog) -> None:
        if result == QDialog.Accepted:
            self.service.resolve_recovery(dialog.chosen_end())
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

    def _install_shortcuts(self) -> None:
        self._shortcuts: list[QShortcut] = []
        for sequence, callback in (
            ("Left", lambda: self._navigate_pages(-1)),
            ("Right", lambda: self._navigate_pages(1)),
            ("Escape", self._handle_escape_shortcut),
            ("W", lambda: self._handle_graph_range_shortcut("weeks")),
            ("M", lambda: self._handle_graph_range_shortcut("months")),
            ("Y", lambda: self._handle_graph_range_shortcut("year")),
            ("A", lambda: self._handle_graph_range_shortcut("all")),
        ):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.WindowShortcut)
            shortcut.setAutoRepeat(False)
            shortcut.activated.connect(callback)
            self._shortcuts.append(shortcut)

        undo_sequences = [QKeySequence.Undo, QKeySequence("Ctrl+Z")]
        seen_undo: set[str] = set()
        for sequence in undo_sequences:
            key = QKeySequence(sequence).toString()
            if not key or key in seen_undo:
                continue
            seen_undo.add(key)
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.WindowShortcut)
            shortcut.setAutoRepeat(False)
            shortcut.activated.connect(self._perform_undo)
            self._shortcuts.append(shortcut)

        for number in range(1, 10):
            shortcut = QShortcut(QKeySequence(str(number)), self)
            shortcut.setContext(Qt.WindowShortcut)
            shortcut.setAutoRepeat(False)
            shortcut.activated.connect(
                lambda selected=number: self._handle_number_shortcut(selected)
            )
            self._shortcuts.append(shortcut)

    def _shortcut_focus_allows_navigation(self) -> bool:
        if int(self.property("_jt_inline_dialog_count") or 0) > 0:
            return False
        if (
            QApplication.activePopupWidget() is not None
            or QApplication.activeModalWidget() is not None
        ):
            return False
        focus = QApplication.focusWidget()
        return not isinstance(
            focus,
            (
                QLineEdit,
                QTextEdit,
                QPlainTextEdit,
                QAbstractSpinBox,
                QComboBox,
            ),
        )

    def _navigate_pages(self, delta: int) -> None:
        if not self._shortcut_focus_allows_navigation():
            return
        current = self._pages.currentIndex()
        target = max(0, min(self._pages.count() - 1, current + int(delta)))
        if target != current:
            self._switch_page(target)

    def _handle_number_shortcut(self, number: int) -> None:
        if not self._shortcut_focus_allows_navigation():
            return
        row = int(number) - 1
        page = self._pages.currentIndex()
        if page == 0:
            if row >= self._todo_list.count():
                return
            goal_id = self._todo_list.item(row).data(Qt.UserRole)
            if goal_id is not None:
                goal_id = int(goal_id)
                self._todo_list.pulse_card(goal_id)
                self._open_goal_after_shortcut_feedback(goal_id)
            return
        if page == 1:
            # Number shortcuts may only START tracking. They never stop or
            # switch an active session, and archived subjects cannot be started.
            if (
                self._showing_archived
                or self.service.active_session is not None
                or row >= self._subjects_list.count()
            ):
                return
            subject_id = self._subjects_list.item(row).data(Qt.UserRole)
            if subject_id is not None:
                self._start_tracking(
                    int(subject_id), shortcut_feedback=True
                )
            return
        if page == 2 and 1 <= number <= 3:
            mode = ("bar", "agenda", "heatmap")[number - 1]
            self._set_graph_view_mode(mode)
            self._pulse_widget(self._graph_mode_buttons[mode])

    def _open_goal_after_shortcut_feedback(self, goal_id: int) -> None:
        if hasattr(self, "_goal_shortcut_timer"):
            self._goal_shortcut_timer.stop()
        self._goal_shortcut_timer = QTimer(self)
        self._goal_shortcut_timer.setSingleShot(True)
        self._goal_shortcut_timer.timeout.connect(
            lambda selected=goal_id: self._open_goal(selected)
        )
        self._goal_shortcut_timer.start(140)

    def _handle_escape_shortcut(self) -> None:
        if not self._shortcut_focus_allows_navigation():
            return
        if self._pages.currentIndex() == 0 and self._showing_completed_goals:
            self._toggle_completed_goals()
        elif self._pages.currentIndex() == 1 and self._showing_archived:
            self._toggle_archived_view()

    def _handle_graph_range_shortcut(self, preset: str) -> None:
        if (
            not self._shortcut_focus_allows_navigation()
            or self._pages.currentIndex() != 2
            or preset not in {"weeks", "months", "year", "all"}
        ):
            return
        self._graph_range_preset = preset
        self._graph_custom_range = None
        self.service.set_setting("graph_range", preset)
        self._reload_graphs()
        self._pulse_widget(self._graph_settings_btn)

    def _pulse_widget(self, widget: QWidget, duration_ms: int = 180) -> None:
        if widget.graphicsEffect() is not None:
            return
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
        anim = QVariantAnimation(self)
        anim.setDuration(max(120, int(duration_ms)))
        anim.setKeyValueAt(0.0, 1.0)
        anim.setKeyValueAt(0.45, 0.62)
        anim.setKeyValueAt(1.0, 1.0)
        anim.setEasingCurve(QEasingCurve.InOutCubic)
        def set_opacity(value) -> None:
            try:
                effect.setOpacity(float(value))
            except RuntimeError:
                anim.stop()
                if anim in self._shortcut_feedback_anims:
                    self._shortcut_feedback_anims.remove(anim)

        anim.valueChanged.connect(set_opacity)

        def finish() -> None:
            try:
                if widget.graphicsEffect() is effect:
                    widget.setGraphicsEffect(None)
            except RuntimeError:
                pass
            if anim in self._shortcut_feedback_anims:
                self._shortcut_feedback_anims.remove(anim)

        anim.finished.connect(finish)
        self._shortcut_feedback_anims.append(anim)
        anim.start()

    def _register_undo(self, callback) -> None:
        """Keep one intentionally small, in-memory reversible action."""
        self._undo_callback = callback

    def _queue_ui_action(self, callback, *args) -> None:
        """Let the originating input event finish before changing main views."""
        def run() -> None:
            callback(*args)

        QTimer.singleShot(0, run)

    def _perform_undo(self, force: bool = False) -> None:
        if not force and not self._shortcut_focus_allows_navigation():
            return
        callback = self._undo_callback
        if callback is None:
            return
        self._undo_callback = None
        callback()
        self._reload()

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

        labels = ["Goals", "Subjects", "Graphs"]
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
        self._heatmap_view.set_tokens(self._tokens)
        self._fx_bg.apply_theme(self._tokens, self._fx)
        self._sync_active_session_indicator()

    def _reload(self) -> None:
        self._reload_subjects()
        self._reload_tasks()
        self._reload_graphs()

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
            self._timer.pulse_stop_button()
            return
        super().closeEvent(event)
