"""Animated, auto-scrolling reorderable list for application cards."""

from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QModelIndex,
    QPoint,
    QPropertyAnimation,
    QRect,
    QSize,
    QTimer,
    QVariantAnimation,
    Qt,
    Signal,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QAbstractItemView,
    QFrame,
    QGraphicsOpacityEffect,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QWidget,
)


class ReorderableCardList(QListWidget):
    order_changed = Signal(object)

    def __init__(self, spacing: int = 8, parent=None) -> None:
        super().__init__(parent)
        self._press_pos = QPoint()
        self._press_id: int | None = None
        self._drag_id: int | None = None
        self._drag_widget: QWidget | None = None
        self._drag_overlay: QLabel | None = None
        self._drag_pixmap = None
        self._drag_scaled_pixmap = None
        self._drag_scaled_size = QSize()
        self._drag_cursor_offset = QPoint()
        self._drag_global_pos = QPoint()
        self._drag_initial_order: list[int] = []
        self._drag_scale = 1.0
        self._wobble_offset = 0
        self._wobble_phase = 1
        self._scale_anim: QVariantAnimation | None = None
        self._drop_anim: QPropertyAnimation | None = None
        self._reflow_anims: list[QPropertyAnimation] = []
        self._reflow_overlays: list[QLabel] = []
        self._remove_anims: dict[int, QVariantAnimation] = {}
        self._pulse_anims: dict[int, QVariantAnimation] = {}
        # id -> structural signature of the card currently rendered for it, so
        # a reconcile can tell a content change (rebuild one card) from a
        # transient change (update in place). Plain dict avoids QVariant issues.
        self._card_signatures: dict[int, object] = {}

        self._wobble_timer = QTimer(self)
        self._wobble_timer.setInterval(70)
        self._wobble_timer.timeout.connect(self._tick_wobble)
        self._auto_scroll_timer = QTimer(self)
        self._auto_scroll_timer.setInterval(16)
        self._auto_scroll_timer.timeout.connect(self._tick_auto_scroll)

        self.setFrameShape(QFrame.NoFrame)
        self.setSpacing(spacing)
        self.setMovement(QListView.Static)
        self.setSelectionMode(QAbstractItemView.NoSelection)
        self.setProperty("_jt_direct_arrow_scroll", True)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setAutoScroll(True)
        self.setAutoScrollMargin(58)

    def add_card(self, item_id: int, widget: QWidget) -> None:
        item = QListWidgetItem()
        item.setData(Qt.UserRole, int(item_id))
        self.addItem(item)
        self._install_card_widget(item, widget)

    def _install_card_widget(self, item: QListWidgetItem, widget: QWidget) -> None:
        """Attach a card widget to a row (used by add_card and reconcile)."""
        # setItemWidget() briefly reparents a widget internally. On Cocoa, an
        # explicitly visible child can momentarily have no QWidget parent
        # during that operation and acquire its own NSWindow. Shortcut badges
        # are the only children we explicitly show while reconciling, so keep
        # them hidden until the index widget is fully installed.
        for badge in widget.findChildren(QLabel):
            if badge.property("_jt_shortcut_badge"):
                badge.hide()
        item.setSizeHint(widget.sizeHint())
        self.setItemWidget(item, widget)
        widget.setProperty("_jt_item_id", int(item.data(Qt.UserRole)))
        install_badge = getattr(widget, "install_shortcut_badge", None)
        if install_badge is not None:
            install_badge()
            item.setSizeHint(widget.sizeHint())
        self._install_drag_filters(widget)

    def sync_cards(self, ids, signatures, make_card, update_card) -> None:
        """Reconcile the rows to ``ids`` (in order) without a full teardown.

        Destroying and recreating every row widget is what glitches a fullscreen
        macOS window (its own Space) into a jump. So instead we diff:

        - rows whose id disappeared are removed;
        - a kept row whose structural ``signatures[id]`` is unchanged is updated
          in place via ``update_card(id, widget)`` (no widget destroyed);
        - a kept row whose signature changed is rebuilt — but only that one card;
        - new ids are built with ``make_card(id)`` and inserted at their slot;
        - order is fixed by moving rows through the model, which preserves the
          existing index widgets.

        The net effect: archiving, adding a session, completing a goal, editing,
        and reordering touch at most the cards that actually changed.
        """
        self._cancel_drag()
        desired = [int(i) for i in ids]
        desired_set = set(desired)

        for row in range(self.count() - 1, -1, -1):
            rid = self.item(row).data(Qt.UserRole)
            if rid is None or int(rid) not in desired_set:
                if rid is not None:
                    self._card_signatures.pop(int(rid), None)
                self._hide_card_for_detach(self.item(row))
                self.takeItem(row)

        for index, item_id in enumerate(desired):
            new_sig = signatures.get(item_id)
            row = self._row_for_id(item_id)
            if row < 0:
                item = QListWidgetItem()
                item.setData(Qt.UserRole, item_id)
                self.insertItem(index, item)
                self._install_card_widget(item, make_card(item_id))
                self._card_signatures[item_id] = new_sig
                continue

            if row != index:
                self._move_item_rows(row, index)
            item = self.item(index)
            if self._card_signatures.get(item_id) != new_sig:
                self._hide_card_for_detach(item)
                self._install_card_widget(item, make_card(item_id))
                self._card_signatures[item_id] = new_sig
            else:
                widget = self.itemWidget(item)
                if widget is not None:
                    update_card(item_id, widget)

        self._renumber_shortcut_badges()

    def clear_cards(self) -> None:
        self._cancel_drag()
        for row in range(self.count()):
            self._hide_card_for_detach(self.item(row))
        self.clear()
        self._card_signatures.clear()
        self._press_id = None

    def _hide_card_for_detach(self, item: QListWidgetItem | None) -> None:
        """Make descendants non-visible before Qt reparents/destroys a card."""
        if item is None:
            return
        widget = self.itemWidget(item)
        if widget is None:
            return
        for badge in widget.findChildren(QLabel):
            if badge.property("_jt_shortcut_badge"):
                badge.hide()
        widget.hide()

    def ordered_ids(self) -> list[int]:
        ids: list[int] = []
        for i in range(self.count()):
            item = self.item(i)
            row_id = item.data(Qt.UserRole)
            if row_id is not None:
                ids.append(int(row_id))
        return ids

    def capture_view_state(self) -> dict:
        """Return transient scroll state to survive a card-list rebuild."""
        return {"scroll": self.verticalScrollBar().value()}

    def restore_view_state(self, state: dict | None) -> None:
        """Restore selection and pixel scroll position after cards are rebuilt."""
        if not state:
            return

        def apply_state() -> None:
            self.verticalScrollBar().setValue(int(state.get("scroll", 0)))

        # QListWidget updates its scroll range after the event loop lays out the
        # newly installed index widgets.
        QTimer.singleShot(0, apply_state)

    def scroll_one_card(self, key: int) -> None:
        """Move the viewport exactly one task card without changing selection."""
        if key not in (Qt.Key_Up, Qt.Key_Down) or self.count() == 0:
            return
        center_x = max(0, self.viewport().width() // 2)
        first = self.itemAt(QPoint(center_x, 1))
        if first is None:
            for row in range(self.count()):
                candidate = self.item(row)
                if self.visualItemRect(candidate).bottom() >= 0:
                    first = candidate
                    break
        if first is None:
            return

        row = self.row(first)
        step_row = row if key == Qt.Key_Down else max(0, row - 1)
        step_item = self.item(step_row)
        height = step_item.sizeHint().height()
        if height <= 0:
            height = self.visualItemRect(step_item).height()
        step = max(1, height + self.spacing())
        direction = -1 if key == Qt.Key_Up else 1
        bar = self.verticalScrollBar()
        bar.setValue(bar.value() + direction * step)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key_Up, Qt.Key_Down):
            self.scroll_one_card(event.key())
            event.accept()
            return
        super().keyPressEvent(event)

    def pulse_card(self, item_id: int, duration_ms: int = 180) -> None:
        """Give one card a restrained opacity pulse as shortcut feedback."""
        row = self._row_for_id(item_id)
        if row < 0:
            return
        widget = self.itemWidget(self.item(row))
        if widget is None or widget.graphicsEffect() is not None:
            return

        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
        effect.setOpacity(1.0)
        anim = QVariantAnimation(self)
        anim.setDuration(max(120, int(duration_ms)))
        anim.setKeyValueAt(0.0, 1.0)
        anim.setKeyValueAt(0.45, 0.68)
        anim.setKeyValueAt(1.0, 1.0)
        anim.setEasingCurve(QEasingCurve.InOutCubic)
        def set_opacity(value) -> None:
            try:
                effect.setOpacity(float(value))
            except RuntimeError:
                # The card may be rebuilt while the brief feedback animation is
                # still running. Its Qt effect is then already destroyed.
                anim.stop()
                self._pulse_anims.pop(int(item_id), None)

        anim.valueChanged.connect(set_opacity)

        def finish() -> None:
            self._pulse_anims.pop(int(item_id), None)
            try:
                if widget.graphicsEffect() is effect:
                    widget.setGraphicsEffect(None)
            except RuntimeError:
                pass

        anim.finished.connect(finish)
        self._pulse_anims[int(item_id)] = anim
        anim.start()

    def contains_id(self, item_id: int) -> bool:
        return self._row_for_id(item_id) >= 0

    def card_widget(self, item_id: int) -> QWidget | None:
        """Return the live card widget for an id, or None if absent."""
        row = self._row_for_id(item_id)
        if row < 0:
            return None
        return self.itemWidget(self.item(row))

    def _renumber_shortcut_badges(self) -> None:
        """Keep visible 1–9 keycaps aligned with the current live row order."""
        for row in range(self.count()):
            widget = self.itemWidget(self.item(row))
            if widget is None:
                continue
            number = row + 1
            for badge in widget.findChildren(QLabel):
                if not badge.property("_jt_shortcut_badge"):
                    continue
                badge.setText(str(number))
                template = badge.property("_jt_shortcut_tooltip")
                if template:
                    badge.setToolTip(
                        str(template).format(number=number)
                    )
                if number > 9:
                    badge.hide()
                elif not badge.isVisible():
                    QTimer.singleShot(
                        0,
                        lambda current=badge: self._show_shortcut_badge(
                            current
                        ),
                    )

    def _show_shortcut_badge(self, badge: QLabel) -> None:
        """Show a badge only after its card has a stable list parent."""
        try:
            parent = badge.parentWidget()
            if parent is not None and parent.parentWidget() is self.viewport():
                badge.show()
        except RuntimeError:
            pass

    def _refresh_drag_pixmap(self) -> None:
        """Repaint the floating card after its live shortcut number changes."""
        if self._drag_widget is None or self._drag_overlay is None:
            return
        pixmap = self._drag_widget.grab()
        if pixmap.isNull():
            return
        self._drag_pixmap = pixmap
        self._drag_scaled_pixmap = pixmap
        self._drag_scaled_size = QSize()
        self._position_drag_overlay()

    def animate_remove(
        self,
        item_id: int,
        on_finished=None,
        duration_ms: int = 320,
    ) -> None:
        """Fade + shrink the card, then remove it. Other rows reflow via sizeHint updates."""
        row = self._row_for_id(item_id)
        if row < 0:
            if on_finished:
                on_finished()
            return

        item = self.item(row)
        widget = self.itemWidget(item)
        if item is None or widget is None:
            self.takeItem(row)
            if on_finished:
                on_finished()
            return

        if not self.isVisible() or not self.viewport().isVisible():
            self.takeItem(row)
            if on_finished:
                on_finished()
            return

        original_height = widget.height()
        original_hint = item.sizeHint()

        # True fade: a graphics opacity effect actually works on child widgets
        # (setWindowOpacity is ignored unless the widget is a top-level window).
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
        effect.setOpacity(1.0)

        anim = QVariantAnimation(self)
        anim.setDuration(max(120, int(duration_ms)))
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.InOutCubic)

        def on_value(v) -> None:
            try:
                f = float(v)
            except (TypeError, ValueError):
                return
            try:
                effect.setOpacity(f)
            except Exception:
                pass
            new_h = max(0, int(original_height * f))
            if f < 1.0:
                widget.setFixedHeight(new_h)
            item.setSizeHint(QSize(original_hint.width(), new_h))

        def on_done() -> None:
            self._remove_anims.pop(item_id, None)
            self._card_signatures.pop(item_id, None)
            # Remove row; list will relayout remaining items.
            cur_row = self._row_for_id(item_id)
            if cur_row >= 0:
                self._hide_card_for_detach(self.item(cur_row))
                taken = self.takeItem(cur_row)
                del taken
            if on_finished:
                on_finished()

        anim.valueChanged.connect(on_value)
        anim.finished.connect(on_done)
        self._remove_anims[item_id] = anim
        anim.start()

    def _move_item_rows(self, source_row: int, target_row: int) -> None:
        if (
            source_row == target_row
            or source_row < 0
            or target_row < 0
            or source_row >= self.count()
            or target_row >= self.count()
        ):
            return
        # Moving through the model preserves the QListWidgetItem's index widget.
        # takeItem()/insertItem() can cause Qt to destroy that widget later.
        destination = target_row if target_row < source_row else target_row + 1
        self.model().moveRow(
            QModelIndex(),
            source_row,
            QModelIndex(),
            destination,
        )

    def eventFilter(self, watched, event) -> bool:
        if not isinstance(watched, QWidget):
            return super().eventFilter(watched, event)

        root = self._resolve_card_root(watched)
        if root is None or watched.property("no_drag"):
            return super().eventFilter(watched, event)

        item_id = root.property("_jt_item_id")
        if item_id is None:
            return super().eventFilter(watched, event)

        if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            if self._drag_id is not None:
                return True
            self._press_id = int(item_id)
            self._press_pos = event.globalPosition().toPoint()
            return super().eventFilter(watched, event)

        if (
            event.type() == QEvent.MouseMove
            and (self._press_id is not None or self._drag_id is not None)
            and (event.buttons() & Qt.LeftButton)
        ):
            global_pos = event.globalPosition().toPoint()
            if self._drag_id is not None:
                self._update_drag(global_pos)
                return True
            if int(item_id) == self._press_id:
                distance = (global_pos - self._press_pos).manhattanLength()
            else:
                distance = 0
            if distance >= QApplication.startDragDistance() and self.dragEnabled():
                drag_id = int(item_id)
                self._press_id = None
                if hasattr(root, "suppress_next_click"):
                    root.suppress_next_click()
                self._begin_drag(drag_id, global_pos)
                return True

        if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            if self._drag_id is not None:
                self._finish_drag()
                return True
            self._press_id = None

        return super().eventFilter(watched, event)

    def _begin_drag(self, item_id: int, global_pos: QPoint) -> None:
        row = self._row_for_id(item_id)
        if row < 0:
            return
        item = self.item(row)
        widget = self.itemWidget(item)
        if item is None or widget is None:
            return

        self._drag_id = item_id
        self._drag_widget = widget
        self._drag_initial_order = self.ordered_ids()
        self._drag_global_pos = global_pos
        self._drag_pixmap = widget.grab()
        self._drag_scaled_pixmap = self._drag_pixmap
        self._drag_scaled_size = self._drag_pixmap.size()
        self._drag_scale = 1.0
        self._wobble_offset = 0
        self._wobble_phase = 1

        widget_top_left = widget.mapToGlobal(QPoint(0, 0))
        self._drag_cursor_offset = global_pos - widget_top_left

        overlay = QLabel(self.viewport())
        overlay.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        overlay.setStyleSheet("background: transparent;")
        overlay.setPixmap(self._drag_pixmap)
        overlay.setGeometry(widget.geometry())
        overlay.raise_()
        overlay.show()
        self._drag_overlay = overlay
        self.viewport().grabMouse()
        for button in widget.findChildren(QAbstractButton):
            button.setDown(False)
        widget.hide()

        self._scale_anim = QVariantAnimation(self)
        self._scale_anim.setDuration(105)
        self._scale_anim.setStartValue(1.0)
        self._scale_anim.setEndValue(0.965)
        self._scale_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._scale_anim.valueChanged.connect(self._set_drag_scale)
        self._scale_anim.start()
        self._wobble_timer.start()
        self._auto_scroll_timer.start()
        self._update_drag(global_pos)

    def _set_drag_scale(self, value) -> None:
        try:
            self._drag_scale = float(value)
        except (TypeError, ValueError):
            return
        self._position_drag_overlay()

    def _update_drag(self, global_pos: QPoint) -> None:
        if self._drag_id is None:
            return
        self._drag_global_pos = global_pos
        self._position_drag_overlay()
        self._live_reorder_at(global_pos)

    def _position_drag_overlay(self) -> None:
        if (
            self._drag_overlay is None
            or self._drag_pixmap is None
            or self._drag_id is None
        ):
            return
        cursor = self.viewport().mapFromGlobal(self._drag_global_pos)
        source_size = self._drag_pixmap.size()
        width = max(1, int(source_size.width() * self._drag_scale))
        height = max(1, int(source_size.height() * self._drag_scale))
        requested_size = QSize(width, height)
        if requested_size != self._drag_scaled_size:
            self._drag_scaled_pixmap = self._drag_pixmap.scaled(
                width,
                height,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            self._drag_scaled_size = requested_size
            self._drag_overlay.setPixmap(self._drag_scaled_pixmap)
        scaled = self._drag_scaled_pixmap
        offset_x = int(self._drag_cursor_offset.x() * self._drag_scale)
        offset_y = int(self._drag_cursor_offset.y() * self._drag_scale)
        x = cursor.x() - offset_x + self._wobble_offset
        y = cursor.y() - offset_y
        self._drag_overlay.setGeometry(x, y, scaled.width(), scaled.height())
        self._drag_overlay.raise_()

    def _tick_wobble(self) -> None:
        if self._drag_id is None:
            self._wobble_timer.stop()
            return
        self._wobble_offset = self._wobble_phase
        self._wobble_phase *= -1
        self._position_drag_overlay()

    def _tick_auto_scroll(self) -> None:
        if self._drag_id is None:
            self._auto_scroll_timer.stop()
            return
        cursor = self.viewport().mapFromGlobal(self._drag_global_pos)
        margin = min(64, max(36, self.viewport().height() // 7))
        delta = 0
        if cursor.y() < margin:
            delta = -max(2, int((margin - cursor.y()) / 3))
        elif cursor.y() > self.viewport().height() - margin:
            delta = max(
                2,
                int((cursor.y() - (self.viewport().height() - margin)) / 3),
            )
        if not delta:
            return
        bar = self.verticalScrollBar()
        old_value = bar.value()
        bar.setValue(old_value + delta)
        if bar.value() != old_value:
            self._position_drag_overlay()
            self._live_reorder_at(self._drag_global_pos)

    def _live_reorder_at(self, global_pos: QPoint) -> None:
        if self._drag_id is None or self.count() < 2:
            return
        cursor = self.viewport().mapFromGlobal(global_pos)
        source_row = self._row_for_id(self._drag_id)
        if source_row < 0:
            return

        target_item = self.itemAt(QPoint(self.viewport().width() // 2, cursor.y()))
        if target_item is None:
            if cursor.y() < 0:
                target_row = 0
            elif cursor.y() > self.viewport().height():
                target_row = self.count() - 1
            else:
                return
        else:
            target_row = self.row(target_item)
            target_rect = self.visualItemRect(target_item)
            if source_row < target_row and cursor.y() < target_rect.center().y():
                target_row -= 1
            elif source_row > target_row and cursor.y() > target_rect.center().y():
                target_row += 1

        target_row = max(0, min(self.count() - 1, target_row))
        if target_row == source_row:
            return
        self._animate_live_move(source_row, target_row)

    def _animate_live_move(self, source_row: int, target_row: int) -> None:
        self._clear_reflow_animations()
        old_rects: dict[int, QRect] = {}
        first_affected = min(source_row, target_row)
        last_affected = max(source_row, target_row)
        for row in range(first_affected, last_affected + 1):
            item = self.item(row)
            row_id = item.data(Qt.UserRole)
            widget = self.itemWidget(item)
            if row_id is None or widget is None or int(row_id) == self._drag_id:
                continue
            old_rects[int(row_id)] = self.visualItemRect(item)

        self._move_item_rows(source_row, target_row)
        self._renumber_shortcut_badges()
        self.doItemsLayout()
        self._refresh_drag_pixmap()
        if self._drag_widget is not None:
            self._drag_widget.hide()

        for row_id, old_rect in old_rects.items():
            new_row = self._row_for_id(row_id)
            if new_row < 0:
                continue
            item = self.item(new_row)
            new_rect = self.visualItemRect(item)
            if old_rect == new_rect:
                continue
            widget = self.itemWidget(item)
            if widget is None:
                continue
            updated_pixmap = widget.grab()
            widget.hide()
            overlay = QLabel(self.viewport())
            overlay.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            # Grab after renumbering so the moving card displays its new
            # shortcut number throughout the reflow animation.
            overlay.setPixmap(updated_pixmap)
            overlay.setGeometry(old_rect)
            overlay.show()

            anim = QPropertyAnimation(overlay, b"geometry", self)
            anim.setDuration(135)
            anim.setEasingCurve(QEasingCurve.OutCubic)
            anim.setStartValue(old_rect)
            anim.setEndValue(new_rect)

            def finish(
                current_widget=widget,
                current_overlay=overlay,
                current_anim=anim,
            ) -> None:
                if current_widget is not self._drag_widget:
                    current_widget.show()
                current_overlay.deleteLater()
                if current_anim in self._reflow_anims:
                    self._reflow_anims.remove(current_anim)
                if current_overlay in self._reflow_overlays:
                    self._reflow_overlays.remove(current_overlay)

            anim.finished.connect(finish)
            self._reflow_anims.append(anim)
            self._reflow_overlays.append(overlay)
            anim.start()
        if self._drag_overlay is not None:
            self._drag_overlay.raise_()

    def _clear_reflow_animations(self) -> None:
        for anim in self._reflow_anims:
            anim.stop()
        self._reflow_anims.clear()
        for overlay in self._reflow_overlays:
            overlay.deleteLater()
        self._reflow_overlays.clear()
        for row in range(self.count()):
            widget = self.itemWidget(self.item(row))
            if widget is not None and widget is not self._drag_widget:
                widget.show()

    def _finish_drag(self) -> None:
        if self._drag_id is None:
            return
        final_order = self.ordered_ids()
        changed = final_order != self._drag_initial_order
        self._wobble_timer.stop()
        self._auto_scroll_timer.stop()
        if QWidget.mouseGrabber() is self.viewport():
            self.viewport().releaseMouse()
        if self._scale_anim is not None:
            self._scale_anim.stop()
            self._scale_anim = None
        self._clear_reflow_animations()

        row = self._row_for_id(self._drag_id)
        target_rect = self.visualItemRect(self.item(row)) if row >= 0 else QRect()
        overlay = self._drag_overlay
        widget = self._drag_widget

        def cleanup() -> None:
            if widget is not None:
                try:
                    widget.show()
                except RuntimeError:
                    pass
            if overlay is not None:
                overlay.deleteLater()
            self._reset_drag_state()
            if changed:
                self.order_changed.emit(final_order)

        if overlay is None or target_rect.isNull():
            cleanup()
            return
        self._drop_anim = QPropertyAnimation(overlay, b"geometry", self)
        self._drop_anim.setDuration(125)
        self._drop_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._drop_anim.setStartValue(overlay.geometry())
        self._drop_anim.setEndValue(target_rect)
        self._drop_anim.finished.connect(cleanup)
        self._drop_anim.start()

    def _cancel_drag(self) -> None:
        if self._drag_id is None:
            return
        self._wobble_timer.stop()
        self._auto_scroll_timer.stop()
        if self._scale_anim is not None:
            self._scale_anim.stop()
            self._scale_anim = None
        if self._drop_anim is not None:
            self._drop_anim.stop()
            self._drop_anim = None
        self._clear_reflow_animations()
        if self._drag_widget is not None:
            self._drag_widget.show()
        if self._drag_overlay is not None:
            self._drag_overlay.deleteLater()
        self._reset_drag_state()

    def _reset_drag_state(self) -> None:
        if QWidget.mouseGrabber() is self.viewport():
            self.viewport().releaseMouse()
        self._drag_id = None
        self._drag_widget = None
        self._drag_overlay = None
        self._drag_pixmap = None
        self._drag_scaled_pixmap = None
        self._drag_scaled_size = QSize()
        self._drag_initial_order = []
        self._drag_scale = 1.0
        self._wobble_offset = 0
        self._drop_anim = None
        self._press_id = None

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_id is not None and (event.buttons() & Qt.LeftButton):
            self._update_drag(event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._drag_id is not None and event.button() == Qt.LeftButton:
            self._finish_drag()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _row_for_id(self, item_id: int) -> int:
        for i in range(self.count()):
            item = self.item(i)
            row_id = item.data(Qt.UserRole)
            if row_id is None:
                continue
            if int(row_id) == int(item_id):
                return i
        return -1

    def _install_drag_filters(self, root_widget: QWidget) -> None:
        root_widget.installEventFilter(self)
        for child in root_widget.findChildren(QWidget):
            child.installEventFilter(self)

    def _resolve_card_root(self, widget: QWidget) -> QWidget | None:
        cur: QWidget | None = widget
        while cur is not None:
            if cur.property("_jt_item_id") is not None:
                return cur
            if cur is self.viewport():
                break
            cur = cur.parentWidget()
        return None
