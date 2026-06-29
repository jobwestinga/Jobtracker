"""
Subject card widget (timed item).

- Click anywhere on the card to start tracking.
- Context menu supports edit/delete/session management.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QVBoxLayout,
)

from ...core.models import Subject


def format_duration(seconds: int) -> str:
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}h {m}m"
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


def _hex_to_rgba(hex_color: str, alpha: int) -> str:
    value = hex_color.lstrip("#")
    if len(value) != 6:
        return hex_color
    try:
        r = int(value[0:2], 16)
        g = int(value[2:4], 16)
        b = int(value[4:6], 16)
    except ValueError:
        return hex_color
    return f"rgba({r}, {g}, {b}, {max(0, min(255, alpha))})"


def _extract_border_color(border_style: str, fallback: str) -> str:
    """Extract a color token from '1px solid <color>' style strings."""
    if not border_style:
        return fallback

    rgba_idx = border_style.find("rgba(")
    if rgba_idx >= 0:
        end = border_style.find(")", rgba_idx)
        if end > rgba_idx:
            return border_style[rgba_idx : end + 1]

    hash_idx = border_style.rfind("#")
    if hash_idx >= 0:
        token = border_style[hash_idx:].split()[0]
        if len(token) in (7, 9):
            return token

    return fallback


class SubjectItemWidget(QFrame):

    start_requested = Signal(int)
    edit_requested = Signal(int)
    delete_requested = Signal(int)
    manage_sessions_requested = Signal(int)
    archive_requested = Signal(int)
    unarchive_requested = Signal(int)

    def __init__(
        self,
        subject: Subject,
        tokens: dict,
        total_seconds: int = 0,
        is_dimmed: bool = False,
        is_active: bool = False,
        is_archived: bool = False,
        shortcut_number: int | None = None,
    ) -> None:
        super().__init__()
        self.subject = subject
        self.total_seconds = total_seconds
        self.is_dimmed = False
        self.is_active = False
        self._is_archived = is_archived
        self._shortcut_number = shortcut_number
        self._t = tokens
        self._press_pos = QPoint()
        self._suppress_click_once = False
        self._base_size_hint: QSize | None = None

        self._build_ui()
        self.is_active = is_active
        self.set_dimmed(is_dimmed)

        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

    def _build_ui(self) -> None:
        t = self._t
        self.setMinimumHeight(78)
        self.setCursor(Qt.PointingHandCursor)
        self._apply_normal_style()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 14, 12)
        layout.setSpacing(12)

        if self._shortcut_number is not None:
            shortcut = QLabel(str(self._shortcut_number))
            shortcut.setProperty("_jt_shortcut_badge", True)
            shortcut.setProperty(
                "_jt_shortcut_tooltip",
                "Press {number} to start tracking",
            )
            shortcut.setAlignment(Qt.AlignCenter)
            shortcut.setFixedSize(22, 22)
            shortcut.setToolTip(f"Press {self._shortcut_number} to start tracking")
            shortcut.setStyleSheet(
                f"background: {t.get('BG_TERTIARY', t['BG_SECONDARY'])};"
                f" border: 1px solid {t['BORDER_COLOR']}; border-radius: 6px;"
                f" color: {t['TEXT_SECONDARY']}; font-size: 10px; font-weight: 750;"
            )
            shortcut.setVisible(1 <= self._shortcut_number <= 9)
            layout.addWidget(shortcut, 0, Qt.AlignTop)

        dot = QLabel()
        dot.setFixedSize(10, 10)
        dot.setStyleSheet(
            f"background-color: {self.subject.color}; border-radius: 5px; border: none;"
        )
        layout.addWidget(dot, 0, Qt.AlignTop | Qt.AlignLeft)

        info = QVBoxLayout()
        info.setSpacing(4)

        self._name_lbl = QLabel(self.subject.name)
        self._name_lbl.setStyleSheet(
            f"font-size: 15px; font-weight: 650; color: {t['TEXT_PRIMARY']}; background: transparent;"
        )
        info.addWidget(self._name_lbl)

        stats_text = format_duration(self.total_seconds)
        self._stats_lbl = QLabel(f"Tracked: {stats_text}" if self.total_seconds else "No time tracked yet")
        color = self.subject.color if self.total_seconds else t["TEXT_DIMMED"]
        self._stats_lbl.setStyleSheet(
            f"font-size: 12px; font-weight: 500; color: {color}; background: transparent;"
        )
        info.addWidget(self._stats_lbl)

        if self.subject.notes:
            notes_lbl = QLabel(self.subject.notes)
            notes_lbl.setWordWrap(True)
            notes_lbl.setStyleSheet(
                f"font-size: 11px; color: {t['TEXT_SECONDARY']}; background: transparent;"
            )
            info.addWidget(notes_lbl)

        hint_lbl = QLabel("Click to start tracking, drag to reorder")
        hint_lbl.setStyleSheet(
            f"font-size: 10px; color: {t['TEXT_DIMMED']}; background: transparent;"
        )
        info.addWidget(hint_lbl)

        layout.addLayout(info)
        layout.addStretch()

        # ── Inline action buttons (right side) ────────────────────────────
        self._action_buttons: list[QPushButton] = []
        if self._is_archived:
            specs = [
                ("Unarchive", self.unarchive_requested, False),
                ("Sessions", self.manage_sessions_requested, False),
                ("Delete", self.delete_requested, True),
            ]
        else:
            specs = [
                ("Edit", self.edit_requested, False),
                ("Sessions", self.manage_sessions_requested, False),
                ("Archive", self.archive_requested, False),
            ]

        for text, signal, danger in specs:
            btn = self._make_action_button(text, signal, danger)
            layout.addWidget(btn, 0, Qt.AlignVCenter)
            self._action_buttons.append(btn)

        # Keep a stable size hint so active border style changes do not shift
        # neighboring cards in the list.
        self._base_size_hint = super().sizeHint()

    def _make_action_button(self, text: str, signal, danger: bool) -> QPushButton:
        t = self._t
        btn = QPushButton(text)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFocusPolicy(Qt.NoFocus)
        btn.setMinimumHeight(30)
        # Don't let the reorderable list treat a button press as a drag handle.
        btn.setProperty("no_drag", True)
        accent = t["ACCENT_RED"] if danger else self.subject.color
        btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {_hex_to_rgba(accent, 28)};
                border: 1.2px solid {_hex_to_rgba(accent, 150)};
                border-radius: 9px;
                padding: 5px 14px;
                font-size: 11px;
                font-weight: 600;
                color: {t['TEXT_PRIMARY']};
            }}
            QPushButton:hover {{
                background-color: {_hex_to_rgba(accent, 70)};
                border-color: {accent};
            }}
            QPushButton:pressed {{
                background-color: {_hex_to_rgba(accent, 110)};
            }}
            """
        )
        if self.subject.id is not None:
            btn.clicked.connect(lambda checked=False: signal.emit(self.subject.id))
        return btn

    # ── Styles ───────────────────────────────────────────────────────────
    def _apply_normal_style(self) -> None:
        t = self._t
        card_bg = t.get("CARD_BG", t["BG_SECONDARY"])
        card_border_color = _hex_to_rgba(self.subject.color, 135)
        radius = t.get("SUBJECT_RADIUS", t.get("RADIUS_MD", "12px"))
        self.setStyleSheet(
            f"""
            SubjectItemWidget {{
                background-color: {card_bg};
                border: 1.4px solid {card_border_color};
                border-left: 5px solid {self.subject.color};
                border-radius: {radius};
            }}
            """
        )

    def _apply_active_style(self) -> None:
        t = self._t
        card_bg = t.get("CARD_BG", t["BG_SECONDARY"])
        radius = t.get("SUBJECT_RADIUS", t.get("RADIUS_MD", "12px"))
        self.setStyleSheet(
            f"""
            SubjectItemWidget {{
                background-color: {card_bg};
                border: 5px solid {self.subject.color};
                border-left: 5px solid {self.subject.color};
                border-radius: {radius};
            }}
            """
        )

    def _apply_hover_style(self) -> None:
        t = self._t
        hover_bg = t.get("CARD_HOVER_BG", t["BG_TERTIARY"])
        hover_border = t.get("CARD_HOVER_BORDER", f"1px solid {self.subject.color}40")
        hover_border_color = _extract_border_color(hover_border, t.get("BORDER_COLOR", self.subject.color))
        radius = t.get("SUBJECT_RADIUS", t.get("RADIUS_MD", "12px"))
        self.setStyleSheet(
            f"""
            SubjectItemWidget {{
                background-color: {hover_bg};
                border: 1.8px solid {hover_border_color};
                border-left: 5px solid {self.subject.color};
                border-radius: {radius};
            }}
            """
        )

    def _apply_dimmed_style(self) -> None:
        t = self._t
        radius = t.get("SUBJECT_RADIUS", t.get("RADIUS_MD", "12px"))
        dim_bg = t.get("BG_PRIMARY_A205", t["BG_PRIMARY"])
        self.setStyleSheet(
            f"""
            SubjectItemWidget {{
                background-color: {dim_bg};
                border: 1px solid {t['BORDER_COLOR']};
                border-left: 5px solid {t['TEXT_DIMMED']};
                border-radius: {radius};
            }}
            """
        )

    # ── Dimming ──────────────────────────────────────────────────────────
    def set_dimmed(self, dimmed: bool) -> None:
        self.is_dimmed = dimmed
        self._apply_state_style()

    def _update_action_opacity(self) -> None:
        # While any subject is tracking, half-dim the inline actions (both on
        # the active card and the dimmed ones) so the tracked topic stays the
        # most prominent element, yet the buttons remain usable.
        half = self.is_dimmed or self.is_active
        for btn in getattr(self, "_action_buttons", []):
            eff = btn.graphicsEffect()
            if not isinstance(eff, QGraphicsOpacityEffect):
                eff = QGraphicsOpacityEffect(btn)
                btn.setGraphicsEffect(eff)
            eff.setOpacity(0.5 if half else 1.0)

    def set_active(self, active: bool) -> None:
        self.is_active = active
        self._apply_state_style()

    def _apply_state_style(self) -> None:
        self._update_action_opacity()
        if self.is_dimmed:
            self._apply_dimmed_style()
            return
        if self.is_active:
            self._apply_active_style()
            return
        self._apply_normal_style()

    # ── Interaction ──────────────────────────────────────────────────────
    def suppress_next_click(self) -> None:
        self._suppress_click_once = True

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._press_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if (
            event.button() == Qt.LeftButton
            and not self.is_dimmed
            and self.subject.id is not None
            and not self._suppress_click_once
        ):
            distance = (event.position().toPoint() - self._press_pos).manhattanLength()
            if distance < QApplication.startDragDistance():
                self._suppress_click_once = False
                self.start_requested.emit(self.subject.id)
                event.accept()
                return
        self._suppress_click_once = False
        super().mouseReleaseEvent(event)

    def enterEvent(self, event) -> None:
        if not self.is_dimmed and not self.is_active:
            self._apply_hover_style()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        if not self.is_dimmed:
            if self.is_active:
                self._apply_active_style()
            else:
                self._apply_normal_style()
        super().leaveEvent(event)

    # ── Context menu ─────────────────────────────────────────────────────
    def _show_context_menu(self, pos) -> None:
        if self.subject.id is None:
            return
        menu = QMenu(self)

        if self._is_archived:
            # Archived subject: allow unarchive, manage sessions, delete
            unarchive = QAction("Unarchive Subject", self)
            unarchive.triggered.connect(lambda: self.unarchive_requested.emit(self.subject.id))
            menu.addAction(unarchive)

            sessions = QAction("Manage Sessions", self)
            sessions.triggered.connect(lambda: self.manage_sessions_requested.emit(self.subject.id))
            menu.addAction(sessions)

            menu.addSeparator()

            delete = QAction("Delete Subject", self)
            delete.triggered.connect(lambda: self.delete_requested.emit(self.subject.id))
            menu.addAction(delete)
        else:
            # Active subject: allow edit, manage sessions, archive (no delete)
            edit = QAction("Edit Subject", self)
            edit.triggered.connect(lambda: self.edit_requested.emit(self.subject.id))
            menu.addAction(edit)

            sessions = QAction("Manage Sessions", self)
            sessions.triggered.connect(lambda: self.manage_sessions_requested.emit(self.subject.id))
            menu.addAction(sessions)

            menu.addSeparator()

            archive = QAction("Archive Subject", self)
            archive.triggered.connect(lambda: self.archive_requested.emit(self.subject.id))
            menu.addAction(archive)

        menu.exec_(self.mapToGlobal(pos))

    def sizeHint(self) -> QSize:
        return self._base_size_hint if self._base_size_hint is not None else super().sizeHint()
