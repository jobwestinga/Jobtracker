"""Native keyboard, focus and undo coordination for one injected window.

Qt objects remain parented to the window. The window retains UI state and its
compatibility callbacks; this controller never owns another service or timer.
"""
from __future__ import annotations
import weakref
from PySide6.QtCore import QEasingCurve, QEvent, QObject, QTimer, QVariantAnimation, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractSpinBox, QAbstractItemView, QApplication,
    QComboBox, QGraphicsOpacityEffect, QLineEdit, QListView, QPlainTextEdit, QTextEdit, QWidget)

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


class KeyboardController:
    def __init__(self, window):
        self.window = window

    def _install_arrow_direction_filter(self) -> None:
        window = self.window
        arrow_filter = getattr(
            window.app_instance, "_jobtracker_arrow_direction_filter", None
        )
        if arrow_filter is None:
            arrow_filter = _LatestArrowDirectionFilter(window.app_instance)
            window.app_instance.installEventFilter(arrow_filter)
            window.app_instance._jobtracker_arrow_direction_filter = arrow_filter

    def _install_shortcuts(self) -> None:
        window = self.window
        window._shortcuts: list[QShortcut] = []
        for sequence, callback in (
            ("Left", lambda: window._navigate_pages(-1)),
            ("Right", lambda: window._navigate_pages(1)),
            ("Escape", window._handle_escape_shortcut),
            ("W", lambda: window._handle_graph_range_shortcut("weeks")),
            ("M", lambda: window._handle_graph_range_shortcut("months")),
            ("Y", lambda: window._handle_graph_range_shortcut("year")),
            ("A", lambda: window._handle_graph_range_shortcut("all")),
        ):
            shortcut = QShortcut(QKeySequence(sequence), window)
            shortcut.setContext(Qt.WindowShortcut)
            shortcut.setAutoRepeat(False)
            shortcut.activated.connect(callback)
            window._shortcuts.append(shortcut)

        undo_sequences = [QKeySequence.Undo, QKeySequence("Ctrl+Z")]
        seen_undo: set[str] = set()
        for sequence in undo_sequences:
            key = QKeySequence(sequence).toString()
            if not key or key in seen_undo:
                continue
            seen_undo.add(key)
            shortcut = QShortcut(QKeySequence(sequence), window)
            shortcut.setContext(Qt.WindowShortcut)
            shortcut.setAutoRepeat(False)
            shortcut.activated.connect(window._perform_undo)
            window._shortcuts.append(shortcut)

        # Kept per-digit so the switch-confirm prompt can temporarily disable
        # the one conflicting key (two live shortcuts on the same key would be
        # ambiguous to Qt and neither would fire).
        window._number_shortcuts: dict[int, QShortcut] = {}
        for number in range(1, 10):
            shortcut = QShortcut(QKeySequence(str(number)), window)
            shortcut.setContext(Qt.WindowShortcut)
            shortcut.setAutoRepeat(False)
            shortcut.activated.connect(
                lambda selected=number: window._handle_number_shortcut(selected)
            )
            window._shortcuts.append(shortcut)
            window._number_shortcuts[number] = shortcut

    def _shortcut_focus_allows_navigation(self) -> bool:
        window = self.window
        if int(window.property("_jt_inline_dialog_count") or 0) > 0:
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
        window = self.window
        if not window._shortcut_focus_allows_navigation():
            return
        current = window._pages.currentIndex()
        target = max(0, min(window._pages.count() - 1, current + int(delta)))
        if target != current:
            window._switch_page(target)

    def _handle_number_shortcut(self, number: int) -> None:
        window = self.window
        if not window._shortcut_focus_allows_navigation():
            return
        row = int(number) - 1
        page = window._pages.currentIndex()
        if page == 0:
            if row >= window._todo_list.count():
                return
            goal_id = window._todo_list.item(row).data(Qt.UserRole)
            if goal_id is not None:
                goal_id = int(goal_id)
                window._todo_list.pulse_card(goal_id)
                window._open_goal_after_shortcut_feedback(goal_id)
            return
        if page == 1:
            # Number shortcuts start tracking when idle; while a session is
            # active they ask to switch (handled inside _start_tracking) and
            # never stop silently. Archived subjects cannot be started.
            if window._showing_archived or row >= window._subjects_list.count():
                return
            subject_id = window._subjects_list.item(row).data(Qt.UserRole)
            if subject_id is not None:
                window._start_tracking(
                    int(subject_id), shortcut_feedback=True
                )
            return
        if page == 2 and 1 <= number <= 2:
            mode = ("bar", "agenda")[number - 1]
            window._set_graph_view_mode(mode)
            window._pulse_widget(window._graph_mode_buttons[mode])

    def _open_goal_after_shortcut_feedback(self, goal_id: int) -> None:
        window = self.window
        if hasattr(window, "_goal_shortcut_timer"):
            window._goal_shortcut_timer.stop()
        window._goal_shortcut_timer = QTimer(window)
        window._goal_shortcut_timer.setSingleShot(True)
        window._goal_shortcut_timer.timeout.connect(
            lambda selected=goal_id: window._open_goal(selected)
        )
        window._goal_shortcut_timer.start(140)

    def _handle_escape_shortcut(self) -> None:
        window = self.window
        if not window._shortcut_focus_allows_navigation():
            return
        if window._pages.currentIndex() == 0 and window._showing_completed_goals:
            window._toggle_completed_goals()
        elif window._pages.currentIndex() == 1 and window._showing_archived:
            window._toggle_archived_view()

    def _handle_graph_range_shortcut(self, preset: str) -> None:
        window = self.window
        if (
            not window._shortcut_focus_allows_navigation()
            or window._pages.currentIndex() != 2
            or preset not in {"weeks", "months", "year", "all"}
        ):
            return
        window._graph_range_preset = preset
        window._graph_custom_range = None
        window.service.set_setting("graph_range", preset)
        window._reload_graphs()
        window._pulse_widget(window._graph_settings_btn)

    def _pulse_widget(self, widget: QWidget, duration_ms: int = 180) -> None:
        window = self.window
        if widget.graphicsEffect() is not None:
            return
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
        anim = QVariantAnimation(window)
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
                if anim in window._shortcut_feedback_anims:
                    window._shortcut_feedback_anims.remove(anim)

        anim.valueChanged.connect(set_opacity)

        def finish() -> None:
            try:
                if widget.graphicsEffect() is effect:
                    widget.setGraphicsEffect(None)
            except RuntimeError:
                pass
            if anim in window._shortcut_feedback_anims:
                window._shortcut_feedback_anims.remove(anim)

        anim.finished.connect(finish)
        window._shortcut_feedback_anims.append(anim)
        anim.start()

    def _register_undo(self, callback) -> None:
        window = self.window
        """Keep one intentionally small, in-memory reversible action."""
        window._undo_callback = callback

    def _queue_ui_action(self, callback, *args) -> None:
        window = self.window
        """Let the originating input event finish before changing main views."""
        def run() -> None:
            callback(*args)

        QTimer.singleShot(0, run)

    def _perform_undo(self, force: bool = False) -> None:
        window = self.window
        if not force and not window._shortcut_focus_allows_navigation():
            return
        callback = window._undo_callback
        if callback is None:
            return
        window._undo_callback = None
        callback()
        window._reload()

