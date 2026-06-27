"""
Reorderable list for subject/task cards.

Uses QListWidget internal move for smooth drag and drop, while still allowing
fully custom QWidget cards as list items.
"""

from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QPoint,
    QPropertyAnimation,
    QSize,
    QVariantAnimation,
    Qt,
    Signal,
)
from PySide6.QtWidgets import (
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
        self._overlay_anim: QPropertyAnimation | None = None
        self._overlay_label: QLabel | None = None
        self._remove_anims: dict[int, QVariantAnimation] = {}

        self.setFrameShape(QFrame.NoFrame)
        self.setSpacing(spacing)
        self.setMovement(QListView.Static)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setDragDropMode(QAbstractItemView.InternalMove)

    def add_card(self, item_id: int, widget: QWidget) -> None:
        item = QListWidgetItem()
        item.setData(Qt.UserRole, int(item_id))
        item.setSizeHint(widget.sizeHint())
        self.addItem(item)
        self.setItemWidget(item, widget)

        widget.setProperty("_jt_item_id", int(item_id))
        self._install_drag_filters(widget)

    def clear_cards(self) -> None:
        self.clear()
        self._press_id = None

    def ordered_ids(self) -> list[int]:
        ids: list[int] = []
        for i in range(self.count()):
            item = self.item(i)
            row_id = item.data(Qt.UserRole)
            if row_id is not None:
                ids.append(int(row_id))
        return ids

    def contains_id(self, item_id: int) -> bool:
        return self._row_for_id(item_id) >= 0

    def animate_reorder(
        self,
        item_id: int,
        new_order_ids: list[int],
        on_finished=None,
        duration_ms: int = 260,
        emit_order_changed: bool = False,
    ) -> None:
        current_ids = self.ordered_ids()
        if set(current_ids) != set(new_order_ids) or item_id not in current_ids:
            if on_finished:
                on_finished()
            return

        source_row = current_ids.index(item_id)
        target_row = new_order_ids.index(item_id)
        if source_row == target_row:
            if on_finished:
                on_finished()
            return

        source_item = self.item(source_row)
        if source_item is None:
            if on_finished:
                on_finished()
            return

        if not self.isVisible() or not self.viewport().isVisible():
            self._move_item_rows(source_row, target_row)
            if emit_order_changed:
                self.order_changed.emit(self.ordered_ids())
            if on_finished:
                on_finished()
            return

        source_widget = self.itemWidget(source_item)
        if source_widget is None:
            self._move_item_rows(source_row, target_row)
            if emit_order_changed:
                self.order_changed.emit(self.ordered_ids())
            if on_finished:
                on_finished()
            return

        start_rect = source_widget.geometry()
        pix = source_widget.grab()

        self.removeItemWidget(source_item)
        moved_item = self.takeItem(source_row)
        self.insertItem(target_row, moved_item)
        self.setItemWidget(moved_item, source_widget)
        source_widget.hide()
        QApplication.processEvents()

        end_rect = source_widget.geometry()

        overlay = QLabel(self.viewport())
        overlay.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        overlay.setPixmap(pix)
        overlay.setGeometry(start_rect)
        overlay.show()

        anim = QPropertyAnimation(overlay, b"geometry", self)
        anim.setDuration(max(80, int(duration_ms)))
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.setStartValue(start_rect)
        anim.setEndValue(end_rect)

        def _finish() -> None:
            source_widget.show()
            overlay.deleteLater()
            self._overlay_anim = None
            self._overlay_label = None
            if emit_order_changed:
                self.order_changed.emit(self.ordered_ids())
            if on_finished:
                on_finished()

        anim.finished.connect(_finish)
        self._overlay_anim = anim
        self._overlay_label = overlay
        anim.start()

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
            # Remove row; list will relayout remaining items.
            cur_row = self._row_for_id(item_id)
            if cur_row >= 0:
                taken = self.takeItem(cur_row)
                del taken
            if on_finished:
                on_finished()

        anim.valueChanged.connect(on_value)
        anim.finished.connect(on_done)
        self._remove_anims[item_id] = anim
        anim.start()

    def _move_item_rows(self, source_row: int, target_row: int) -> None:
        item = self.item(source_row)
        if item is None:
            return
        widget = self.itemWidget(item)
        if widget is not None:
            self.removeItemWidget(item)
        moved_item = self.takeItem(source_row)
        self.insertItem(target_row, moved_item)
        if widget is not None:
            self.setItemWidget(moved_item, widget)

    def dropEvent(self, event) -> None:
        before = self.ordered_ids()
        super().dropEvent(event)
        after = self.ordered_ids()
        if before != after:
            self.order_changed.emit(after)

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
            self._press_id = int(item_id)
            self._press_pos = event.globalPosition().toPoint()
            return super().eventFilter(watched, event)

        if (
            event.type() == QEvent.MouseMove
            and self._press_id is not None
            and int(item_id) == self._press_id
            and (event.buttons() & Qt.LeftButton)
        ):
            distance = (event.globalPosition().toPoint() - self._press_pos).manhattanLength()
            if distance >= QApplication.startDragDistance():
                self._press_id = None
                if hasattr(root, "suppress_next_click"):
                    root.suppress_next_click()
                self._start_drag_for_id(int(item_id))
                return True

        if event.type() == QEvent.MouseButtonRelease:
            self._press_id = None

        return super().eventFilter(watched, event)

    def _start_drag_for_id(self, item_id: int) -> None:
        row = self._row_for_id(item_id)
        if row < 0:
            return
        self.setCurrentRow(row)
        self.startDrag(Qt.MoveAction)

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
