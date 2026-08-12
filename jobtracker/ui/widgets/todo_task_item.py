"""
Goal card widget (legacy filename retained for compatibility).
"""

from __future__ import annotations

import math
import random

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPoint,
    QPointF,
    QPropertyAnimation,
    Qt,
    Signal,
)
from PySide6.QtGui import QAction, QBrush, QColor, QPainter, QPolygonF
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QProgressBar,
    QPushButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from ...core.models import TodoTask
from .labels import ElidedLabel

# Default celebratory star palette (used when no theme colors are supplied).
_DEFAULT_STAR_COLORS = ["#FACC15", "#22C55E", "#FDE68A", "#FFFFFF"]


class _StarBurstOverlay(QWidget):
    """Transparent overlay that paints outward-flying stars from origin."""

    def __init__(
        self,
        parent: QWidget,
        origin: QPoint,
        count: int = 14,
        colors: list | None = None,
    ) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setGeometry(parent.rect())
        self._origin = QPointF(origin)
        self._progress = 0.0

        palette = [QColor(c) for c in (colors or _DEFAULT_STAR_COLORS)]
        self._particles: list[dict] = []
        rng = random.Random()
        for _ in range(count):
            angle = rng.uniform(0, 2 * math.pi)
            distance = rng.uniform(22, 52)
            self._particles.append(
                {
                    "dx": math.cos(angle) * distance,
                    "dy": math.sin(angle) * distance,
                    "size": rng.uniform(2.0, 4.5),
                    "rot": rng.uniform(0, 360),
                    "color": rng.choice(palette),
                }
            )

        self._anim = QPropertyAnimation(self, b"progress", self)
        self._anim.setDuration(520)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.finished.connect(self.deleteLater)

    def start(self) -> None:
        self.show()
        self.raise_()
        self._anim.start()

    def _get_progress(self) -> float:
        return self._progress

    def _set_progress(self, v: float) -> None:
        self._progress = v
        self.update()

    progress = Property(float, _get_progress, _set_progress)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        t = self._progress
        alpha = 1.0 - t
        for part in self._particles:
            cx = self._origin.x() + part["dx"] * t
            cy = self._origin.y() + part["dy"] * t - (10 * t * t)  # slight gravity arc
            size = part["size"] * (1.0 - 0.35 * t)
            color = QColor(part["color"])
            color.setAlphaF(max(0.0, alpha))
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(color))
            p.save()
            p.translate(cx, cy)
            p.rotate(part["rot"] + 180 * t)
            p.drawPolygon(_star_polygon(size))
            p.restore()
        p.end()


def _star_polygon(radius: float, points: int = 5) -> QPolygonF:
    inner = radius * 0.45
    poly = QPolygonF()
    for i in range(points * 2):
        r = radius if i % 2 == 0 else inner
        angle = (i * math.pi / points) - math.pi / 2
        poly.append(QPointF(math.cos(angle) * r, math.sin(angle) * r))
    return poly


class _CheckButton(QPushButton):
    """Quiet completion ring that only turns green on hover.

    A permanently saturated green circle fought with the card's own palette;
    this reads as part of the row until you reach for it.
    """

    def __init__(self, tokens: dict | None = None, parent=None) -> None:
        super().__init__(parent)
        tokens = tokens or {}
        self.setFixedSize(22, 22)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Complete goal")
        self.setProperty("no_drag", True)
        self.setFlat(True)
        idle = tokens.get("TEXT_DIMMED", "#6F7D90")
        done = tokens.get("ACCENT_GREEN", "#22C55E")
        self.setStyleSheet(
            "QPushButton { background-color: transparent; border: 1.4px solid "
            f"{idle}; border-radius: 11px; color: {idle}; font-weight: 800;"
            " font-size: 11px; }"
            f"QPushButton:hover {{ border-color: {done}; color: {done}; }}"
        )
        self.setText("✓")


class TodoTaskItemWidget(QFrame):
    edit_requested = Signal(int)
    delete_requested = Signal(int)
    complete_requested = Signal(int)
    open_requested = Signal(int)
    focus_toggle_requested = Signal(int)

    def __init__(
        self,
        todo_task: TodoTask,
        tokens: dict,
        progress: tuple = (0, 0),
        shortcut_number: int | None = None,
        parent=None,
    ) -> None:
        # Keep the complete card subtree inside the list's native window from
        # the moment construction begins; see SubjectItemWidget.
        super().__init__(parent)
        self.todo_task = todo_task
        self._t = tokens
        self._completing = False
        self._progress = progress  # (done, total) milestones
        self._shortcut_number = shortcut_number
        self._suppress_click_once = False
        self._press_pos = QPoint()

        self._build_ui()
        self._apply_style()

        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

    def _build_ui(self) -> None:
        t = self._t
        self.setObjectName("todoTaskCard")
        self.setMinimumHeight(72)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 11, 12, 11)
        layout.setSpacing(8)
        self._root_layout = layout
        self._shortcut_badge = None

        info = QVBoxLayout()
        info.setSpacing(3)

        self.name_lbl = ElidedLabel(self.todo_task.name)
        self.name_lbl.setStyleSheet(
            f"font-size: 14px; font-weight: 650; color: {t['TEXT_PRIMARY']}; background: transparent;"
        )
        info.addWidget(self.name_lbl)

        self.progress_lbl = QLabel()
        info.addWidget(self.progress_lbl)

        # Thin milestone progress bar, shown only when there are milestones.
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(3)
        self.progress_bar.setMaximumWidth(180)
        info.addWidget(self.progress_bar)
        self._apply_progress_label()

        if getattr(self.todo_task, "deadline", None):
            target_lbl = QLabel(f"🎯 Target: {self.todo_task.deadline[:10]}")
            target_lbl.setStyleSheet(
                f"font-size: 11px; font-weight: 600; color: {t['TEXT_SECONDARY']}; background: transparent;"
            )
            info.addWidget(target_lbl)

        if self.todo_task.notes:
            notes_lbl = ElidedLabel(self.todo_task.notes)
            notes_lbl.setStyleSheet(
                f"font-size: 11px; color: {t['TEXT_SECONDARY']}; background: transparent;"
            )
            notes_lbl.setToolTip(self.todo_task.notes)
            info.addWidget(notes_lbl)

        # The ✓ sits beside the title rather than at the far edge of a wide card.
        self.check_btn = _CheckButton(t, self)
        if self.todo_task.is_completed:
            self.check_btn.hide()
        self.check_btn.clicked.connect(self._on_check_clicked)
        layout.addWidget(self.check_btn, 0, Qt.AlignVCenter)

        # No trailing stretch: the info column owns the free width, otherwise the
        # description elides at half the card and leaves a dead gap.
        layout.addLayout(info, 1)

        # Weekly-focus star: quiet outline when off, theme accent when on.
        self.focus_btn = QPushButton(self)
        self.focus_btn.setFixedSize(22, 22)
        self.focus_btn.setCursor(Qt.PointingHandCursor)
        self.focus_btn.setFlat(True)
        self.focus_btn.setProperty("no_drag", True)
        self._apply_focus_button_style()
        if self.todo_task.is_completed:
            self.focus_btn.hide()
        self.focus_btn.clicked.connect(self._on_focus_clicked)
        layout.addWidget(self.focus_btn, 0, Qt.AlignVCenter)

    def install_shortcut_badge(self) -> None:
        """Create the keycap only after the card is owned by its list."""
        if self._shortcut_number is None or self._shortcut_badge is not None:
            return
        t = self._t
        shortcut = QLabel(str(self._shortcut_number), self)
        shortcut.setProperty("_jt_shortcut_badge", True)
        shortcut.setProperty(
            "_jt_shortcut_tooltip",
            "Press {number} to open this goal",
        )
        shortcut.setAlignment(Qt.AlignCenter)
        shortcut.setFixedSize(22, 22)
        shortcut.setToolTip(
            f"Press {self._shortcut_number} to open this goal"
        )
        shortcut.setStyleSheet(
            f"background: {t.get('BG_TERTIARY', t['BG_SECONDARY'])};"
            f" border: 1px solid {t['BORDER_COLOR']}; border-radius: 6px;"
            f" color: {t['TEXT_SECONDARY']}; font-size: 10px; font-weight: 750;"
        )
        self._root_layout.insertWidget(0, shortcut, 0, Qt.AlignTop)
        self._shortcut_badge = shortcut

    def _apply_progress_label(self) -> None:
        t = self._t
        done, total = self._progress
        if self.todo_task.is_completed:
            text = (
                f"Completed · {done}/{total} milestones"
                if total > 0 else "Completed"
            )
            color = t["ACCENT_GREEN"]
        elif total > 0:
            text = f"✓ {done}/{total} milestones"
            color = t["ACCENT_GREEN"] if done == total else t["TEXT_SECONDARY"]
        else:
            text = "No milestones — complete manually"
            color = t["TEXT_DIMMED"]
        self.progress_lbl.setText(text)
        self.progress_lbl.setStyleSheet(
            f"font-size: 11px; font-weight: 600; color: {color}; background: transparent;"
        )
        self._apply_progress_bar(done, total, color)

    def _apply_progress_bar(self, done: int, total: int, color: str) -> None:
        bar = getattr(self, "progress_bar", None)
        if bar is None:
            return
        if total <= 0:
            bar.hide()
            return
        bar.show()
        bar.setMaximum(total)
        bar.setValue(done)
        # Chained gets: a caller may pass a partial token dict.
        # Deliberately understated: a hairline groove, no border. It should
        # read as a subtle fill next to the milestone count, not as chrome.
        track = self._t.get("BG_TERTIARY") or self._t.get("BG_SECONDARY", "#161B24")
        bar.setStyleSheet(
            "QProgressBar { border: none; border-radius: 1px; background: "
            f"{track};"
            " } QProgressBar::chunk { border-radius: 1px; background: "
            f"{color}; }}"
        )

    def apply_transient(self, todo_task: TodoTask, progress: tuple) -> None:
        """Refresh the volatile bits in place (no card rebuild): milestone
        progress and completion state. Structural fields (title, notes, target)
        are covered by the reconcile signature and rebuild the card instead."""
        self.todo_task = todo_task
        self._progress = progress
        self._apply_progress_label()
        self.check_btn.setVisible(not todo_task.is_completed)
        # Completion also decides whether the focus star applies, so keep the
        # star and the card outline consistent with the refreshed state.
        self.focus_btn.setVisible(not todo_task.is_completed)
        self._apply_focus_button_style()
        self._apply_style()

    def _apply_style(self) -> None:
        t = self._t
        radius = t.get("TASK_RADIUS", "6px")
        card_bg = t.get("CARD_BG", t["BG_SECONDARY"])
        if self._is_focused():
            # Weekly-focus emphasis: theme-accent outline + left bar. Clear
            # contrast against normal cards without shouting.
            accent = t.get("ACCENT", "#3B82F6")
            self.setStyleSheet(
                f"""
                QFrame#todoTaskCard {{
                    background-color: {card_bg};
                    border: 1.6px solid {accent};
                    border-left: 4px solid {accent};
                    border-radius: {radius};
                }}
                """
            )
            return
        self.setStyleSheet(
            f"""
            QFrame#todoTaskCard {{
                background-color: {card_bg};
                border: 1px solid {t["BORDER_COLOR"]};
                border-radius: {radius};
            }}
            """
        )

    def _is_focused(self) -> bool:
        return bool(
            getattr(self.todo_task, "is_focused", 0)
            and not self.todo_task.is_completed
        )

    def _apply_focus_button_style(self) -> None:
        t = self._t
        accent = t.get("ACCENT", "#3B82F6")
        focused = self._is_focused()
        self.focus_btn.setText("★" if focused else "☆")
        self.focus_btn.setToolTip(
            "Remove from this week's focus" if focused else "Focus on this goal this week"
        )
        color = accent if focused else t.get("TEXT_DIMMED", "#6F7D90")
        self.focus_btn.setStyleSheet(
            "QPushButton { background-color: transparent; border: none; "
            f"color: {color}; font-size: 15px; font-weight: 600; "
            "} QPushButton:hover { "
            f"color: {accent}; "
            "}"
        )

    def _on_focus_clicked(self) -> None:
        if self.todo_task.id is not None:
            self.focus_toggle_requested.emit(self.todo_task.id)

    def _can_complete(self) -> bool:
        """A goal can be completed when it has no milestones or all are done."""
        done, total = self._progress
        return total == 0 or done >= total

    def _star_colors(self) -> list:
        t = self._t
        return [
            t.get("ACCENT_GREEN", "#22C55E"),
            t.get("ACCENT", "#3B82F6"),
            t.get("TEXT_PRIMARY", "#FFFFFF"),
            t.get("ACCENT_GREEN", "#22C55E"),
        ]

    def _shake_check(self) -> None:
        """Brief horizontal shake on the ✓ to signal 'not yet'."""
        start = self.check_btn.pos()
        anim = QPropertyAnimation(self.check_btn, b"pos", self)
        anim.setDuration(300)
        anim.setKeyValueAt(0.0, start)
        anim.setKeyValueAt(0.2, start + QPoint(-5, 0))
        anim.setKeyValueAt(0.4, start + QPoint(5, 0))
        anim.setKeyValueAt(0.6, start + QPoint(-3, 0))
        anim.setKeyValueAt(0.8, start + QPoint(3, 0))
        anim.setKeyValueAt(1.0, start)
        anim.start()
        self._shake_anim = anim  # keep a reference so it isn't GC'd mid-run

    def _on_check_clicked(self) -> None:
        if self._completing or self.todo_task.id is None or self.todo_task.is_completed:
            return

        if not self._can_complete():
            # Gentle "not yet": no celebration, no blocking dialog.
            self._shake_check()
            QToolTip.showText(
                self.check_btn.mapToGlobal(QPoint(self.check_btn.width() // 2, -2)),
                "Finish all milestones first",
                self.check_btn,
            )
            return

        self._completing = True
        self.check_btn.setEnabled(False)

        # Themed star burst centered on the button.
        btn_center = self.check_btn.geometry().center()
        overlay = _StarBurstOverlay(self, btn_center, count=16, colors=self._star_colors())
        overlay.start()

        # Emit so the parent can coordinate list reflow + persistence.
        self.complete_requested.emit(self.todo_task.id)

    def suppress_next_click(self) -> None:
        self._suppress_click_once = True

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._press_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if (
            event.button() == Qt.LeftButton
            and self.todo_task.id is not None
            and not self._suppress_click_once
        ):
            distance = (event.position().toPoint() - self._press_pos).manhattanLength()
            if distance < QApplication.startDragDistance():
                self.open_requested.emit(self.todo_task.id)
                event.accept()
                return
        self._suppress_click_once = False
        super().mouseReleaseEvent(event)

    def _show_context_menu(self, pos) -> None:
        if self.todo_task.id is None:
            return
        menu = QMenu(self)

        open_action = QAction("Open Goal", self)
        open_action.triggered.connect(lambda: self.open_requested.emit(self.todo_task.id))
        menu.addAction(open_action)

        if not self.todo_task.is_completed:
            focus_action = QAction(
                "Remove Weekly Focus" if self._is_focused() else "Focus This Week",
                self,
            )
            focus_action.triggered.connect(
                lambda: self.focus_toggle_requested.emit(self.todo_task.id)
            )
            menu.addAction(focus_action)

        edit = QAction("Edit Goal", self)
        edit.triggered.connect(lambda: self.edit_requested.emit(self.todo_task.id))
        menu.addAction(edit)

        delete = QAction("Delete Goal", self)
        delete.triggered.connect(lambda: self.delete_requested.emit(self.todo_task.id))
        menu.addAction(delete)

        menu.exec_(self.mapToGlobal(pos))
