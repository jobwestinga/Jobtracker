"""
Completable task card widget.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QVBoxLayout,
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


class TodoTaskItemWidget(QFrame):
    edit_requested = Signal(int)
    delete_requested = Signal(int)

    def __init__(self, todo_task: TodoTask, tokens: dict) -> None:
        super().__init__()
        self.todo_task = todo_task
        self._t = tokens

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

        self.deadline_lbl = QLabel(_format_deadline(self.todo_task.deadline))
        deadline_color = t["ACCENT_RED"] if self.todo_task.deadline else t["TEXT_DIMMED"]
        self.deadline_lbl.setStyleSheet(
            f"font-size: 11px; font-weight: 600; color: {deadline_color}; background: transparent;"
        )
        info.addWidget(self.deadline_lbl)

        if self.todo_task.notes:
            notes_lbl = QLabel(self.todo_task.notes)
            notes_lbl.setWordWrap(True)
            notes_lbl.setStyleSheet(
                f"font-size: 11px; color: {t['TEXT_SECONDARY']}; background: transparent;"
            )
            info.addWidget(notes_lbl)

        layout.addLayout(info)
        layout.addStretch()

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

    def _show_context_menu(self, pos) -> None:
        if self.todo_task.id is None:
            return
        menu = QMenu(self)

        edit = QAction("Edit Task", self)
        edit.triggered.connect(lambda: self.edit_requested.emit(self.todo_task.id))
        menu.addAction(edit)

        delete = QAction("Delete Task", self)
        delete.triggered.connect(lambda: self.delete_requested.emit(self.todo_task.id))
        menu.addAction(delete)

        menu.exec_(self.mapToGlobal(pos))
