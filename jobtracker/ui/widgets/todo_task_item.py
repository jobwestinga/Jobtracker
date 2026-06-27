"""
Goal card widget (legacy filename retained for compatibility).
"""

from __future__ import annotations

import math
import random
from datetime import datetime

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
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core.models import TodoTask


def _format_deadline(deadline: str | None) -> str:
    if not deadline:
        return "No deadline"
    try:
        dt = datetime.fromisoformat(deadline)
        return dt.strftime("Due %Y-%m-%d %H:%M")
    except ValueError:
        return f"Due {deadline}"


class _StarBurstOverlay(QWidget):
    """Transparent overlay that paints outward-flying stars from origin."""

    def __init__(self, parent: QWidget, origin: QPoint, count: int = 14) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setGeometry(parent.rect())
        self._origin = QPointF(origin)
        self._progress = 0.0

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
                    "color": rng.choice(
                        [
                            QColor("#FACC15"),
                            QColor("#22C55E"),
                            QColor("#FDE68A"),
                            QColor("#FFFFFF"),
                        ]
                    ),
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
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(28, 28)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Complete goal")
        self.setProperty("no_drag", True)
        self.setFlat(True)
        self.setStyleSheet(
            """
            QPushButton {
                background-color: transparent;
                border: 1.5px solid #22C55E;
                border-radius: 14px;
                color: #22C55E;
                font-weight: 900;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #22C55E;
                color: white;
            }
            """
        )
        self.setText("✓")


class TodoTaskItemWidget(QFrame):
    edit_requested = Signal(int)
    delete_requested = Signal(int)
    complete_requested = Signal(int)
    reopen_requested = Signal(int)
    open_requested = Signal(int)

    def __init__(self, todo_task: TodoTask, tokens: dict, progress: tuple = (0, 0)) -> None:
        super().__init__()
        self.todo_task = todo_task
        self._t = tokens
        self._completing = False
        self._progress = progress  # (done, total) milestones
        self._suppress_click_once = False
        self._press_pos = QPoint()

        self._build_ui()
        self._apply_style()

        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

    def _build_ui(self) -> None:
        t = self._t
        self.setObjectName("todoTaskCard")
        self.setMinimumHeight(60)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 12, 8)
        layout.setSpacing(8)

        info = QVBoxLayout()
        info.setSpacing(3)

        self.name_lbl = QLabel(self.todo_task.name)
        self.name_lbl.setStyleSheet(
            f"font-size: 14px; font-weight: 650; color: {t['TEXT_PRIMARY']}; background: transparent;"
        )
        info.addWidget(self.name_lbl)

        done, total = self._progress
        if self.todo_task.is_completed:
            progress_text = (
                f"Completed · {done}/{total} milestones"
                if total > 0 else "Completed"
            )
            progress_color = t["ACCENT_GREEN"]
        elif total > 0:
            progress_text = f"✓ {done}/{total} milestones"
            progress_color = t["ACCENT_GREEN"] if done == total else t["TEXT_SECONDARY"]
        else:
            progress_text = "No milestones — complete manually"
            progress_color = t["TEXT_DIMMED"]
        self.progress_lbl = QLabel(progress_text)
        self.progress_lbl.setStyleSheet(
            f"font-size: 11px; font-weight: 600; color: {progress_color}; background: transparent;"
        )
        info.addWidget(self.progress_lbl)

        if self.todo_task.notes:
            notes_lbl = QLabel(self.todo_task.notes)
            notes_lbl.setWordWrap(True)
            notes_lbl.setStyleSheet(
                f"font-size: 11px; color: {t['TEXT_SECONDARY']}; background: transparent;"
            )
            info.addWidget(notes_lbl)

        layout.addLayout(info)
        layout.addStretch()

        self.open_btn = QPushButton("Open")
        self.open_btn.setProperty("no_drag", True)
        self.open_btn.setCursor(Qt.PointingHandCursor)
        self.open_btn.setMinimumHeight(28)
        self.open_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: 1.2px solid {t['BORDER_COLOR']};"
            f" border-radius: 8px; padding: 4px 12px; font-size: 11px; font-weight: 600;"
            f" color: {t['TEXT_PRIMARY']}; }}"
            f" QPushButton:hover {{ border-color: {t['ACCENT']}; }}"
        )
        if self.todo_task.id is not None:
            self.open_btn.clicked.connect(lambda: self.open_requested.emit(self.todo_task.id))
        layout.addWidget(self.open_btn, 0, Qt.AlignVCenter)

        self.check_btn = _CheckButton(self)
        if self.todo_task.is_completed:
            self.check_btn.setText("↺")
            self.check_btn.setToolTip("Reopen goal")
        self.check_btn.clicked.connect(self._on_check_clicked)
        layout.addWidget(self.check_btn, 0, Qt.AlignVCenter)

    def _apply_style(self) -> None:
        t = self._t
        radius = t.get("TASK_RADIUS", "6px")
        border_style = "1px solid"
        border_color = t["BORDER_COLOR"]
        card_bg = t.get("CARD_BG", t["BG_SECONDARY"])
        self.setStyleSheet(
            f"""
            QFrame#todoTaskCard {{
                background-color: {card_bg};
                border: {border_style} {border_color};
                border-radius: {radius};
            }}
            """
        )

    def _on_check_clicked(self) -> None:
        if self._completing or self.todo_task.id is None:
            return
        if self.todo_task.is_completed:
            self.reopen_requested.emit(self.todo_task.id)
            return

        self._completing = True
        self.check_btn.setEnabled(False)

        # Spawn star burst centered on the button
        btn_center = self.check_btn.geometry().center()
        overlay = _StarBurstOverlay(self, btn_center, count=16)
        overlay.start()

        # Emit after short delay so parent can coordinate list reflow
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

        if self.todo_task.is_completed:
            reopen = QAction("Reopen Goal", self)
            reopen.triggered.connect(lambda: self.reopen_requested.emit(self.todo_task.id))
            menu.addAction(reopen)

        edit = QAction("Edit Goal", self)
        edit.triggered.connect(lambda: self.edit_requested.emit(self.todo_task.id))
        menu.addAction(edit)

        delete = QAction("Delete Goal", self)
        delete.triggered.connect(lambda: self.delete_requested.emit(self.todo_task.id))
        menu.addAction(delete)

        menu.exec_(self.mapToGlobal(pos))
