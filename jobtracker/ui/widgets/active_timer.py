"""
Timer area widget — always visible.

Idle state:  motivational message, dashed border
Active state: live clock, subject name, stop button
Uses QStackedWidget and a fixed height so switching states never shifts the layout.
"""

from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QStackedWidget,
)
from PySide6.QtCore import QTimer, Signal, Qt
from datetime import datetime
from ...core.models import Session, Subject
from ...core import timeutils


IDLE_MESSAGE = "Get to work."


class ActiveTimerWidget(QFrame):
    """Always-visible timer area with idle and active states."""

    stop_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.subject: Subject | None = None
        self.session: Session | None = None
        self._tokens: dict = {}

        self._tick = QTimer(self)
        self._tick.timeout.connect(self._update_time)

        self.setObjectName("timerArea")
        self.setFixedHeight(200)

        self._build_ui()
        self._show_idle()

    # ── UI construction ──────────────────────────────────────────────────
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self._stack = QStackedWidget()
        outer.addWidget(self._stack)

        # Page 0 — idle
        idle_page = QFrame()
        idle_page.setObjectName("idlePage")
        idle_lay = QVBoxLayout(idle_page)
        idle_lay.setContentsMargins(28, 24, 28, 24)
        idle_lay.setSpacing(8)
        idle_lay.addStretch()

        self._idle_label = QLabel()
        self._idle_label.setObjectName("idleMessage")
        self._idle_label.setAlignment(Qt.AlignCenter)
        self._idle_label.setWordWrap(True)
        self._idle_label.setStyleSheet(
            "font-size: 16px; font-weight: 600; background: transparent;"
        )
        idle_lay.addWidget(self._idle_label)

        idle_hint = QLabel("▶  Click any subject card to start")
        idle_hint.setObjectName("subtitle")
        idle_hint.setAlignment(Qt.AlignCenter)
        idle_lay.addWidget(idle_hint)

        idle_lay.addStretch()
        self._stack.addWidget(idle_page)

        # Page 1 — active
        active_page = QFrame()
        active_page.setObjectName("activePage")
        active_lay = QVBoxLayout(active_page)
        active_lay.setContentsMargins(28, 20, 28, 20)
        active_lay.setSpacing(4)

        header = QLabel("CURRENTLY TRACKING")
        header.setObjectName("subtitle")
        header.setAlignment(Qt.AlignCenter)
        active_lay.addWidget(header)

        self._name = QLabel()
        self._name.setAlignment(Qt.AlignCenter)
        self._name.setStyleSheet("font-size: 20px; font-weight: 700; background: transparent;")
        active_lay.addWidget(self._name)

        active_lay.addSpacing(2)

        self._clock = QLabel("00:00:00")
        self._clock.setAlignment(Qt.AlignCenter)
        self._clock.setStyleSheet(
            "font-size: 48px; font-weight: 700;"
            " font-family: 'SF Mono', 'Menlo', monospace;"
            " letter-spacing: 2px; background: transparent;"
        )
        active_lay.addWidget(self._clock)

        active_lay.addSpacing(4)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._stop_btn = QPushButton("■  Stop Tracking")
        self._stop_btn.setObjectName("stopBtn")
        self._stop_btn.setMinimumWidth(170)
        self._stop_btn.setMinimumHeight(40)
        self._stop_btn.setCursor(Qt.PointingHandCursor)
        self._stop_btn.clicked.connect(self.stop_requested.emit)
        btn_row.addWidget(self._stop_btn)
        btn_row.addStretch()
        active_lay.addLayout(btn_row)

        self._stack.addWidget(active_page)

    # ── Public API ───────────────────────────────────────────────────────
    def apply_tokens(self, tokens: dict) -> None:
        """Update colours to match current theme. Called after theme changes."""
        self._tokens = tokens
        if self.subject:
            self._apply_active_style()
        else:
            self._apply_idle_style()

    def set_active(self, subject: Subject, session: Session) -> None:
        self.subject = subject
        self.session = session
        self._name.setText(subject.name)
        self._apply_active_style()
        self._update_time()
        self._tick.start(500)
        self._stack.setCurrentIndex(1)

    def clear(self) -> None:
        """Switch to idle. Safe to call even when already idle."""
        if self.subject is None and not self._tick.isActive():
            return
        self._tick.stop()
        self.subject = None
        self.session = None
        self._show_idle()

    # ── Private ──────────────────────────────────────────────────────────
    def _show_idle(self) -> None:
        self._idle_label.setText(IDLE_MESSAGE)
        self._apply_idle_style()
        self._stack.setCurrentIndex(0)

    def _apply_idle_style(self) -> None:
        t = self._tokens
        if not t:
            return
        idle_border = t.get("IDLE_BORDER", f"1.5px dashed {t.get('BORDER_COLOR', '#1E3050')}")
        radius = t.get("RADIUS", "14px")
        self.setStyleSheet(f"""
            QFrame#timerArea {{
                background-color: {t['BG_PRIMARY']};
                border: {idle_border};
                border-radius: {radius};
            }}
        """)
        self._idle_label.setStyleSheet(
            f"font-size: 16px; font-weight: 600; color: {t['TEXT_DIMMED']}; background: transparent;"
        )

    def _apply_active_style(self) -> None:
        t = self._tokens
        if not t or not self.subject:
            return
        c = self.subject.color
        timer_bg = t.get("TIMER_BG", t["BG_SECONDARY"])
        timer_border = t.get("TIMER_BORDER", f"1.5px solid {c}")
        # Replace ACCENT with the active subject color in timer border.
        accent = t.get("ACCENT")
        if accent:
            timer_border = timer_border.replace(accent, c)
        radius = t.get("RADIUS", "14px")
        self.setStyleSheet(f"""
            QFrame#timerArea {{
                background: {timer_bg};
                border: {timer_border};
                border-radius: {radius};
            }}
        """)
        self._name.setStyleSheet(
            f"font-size: 20px; font-weight: 700; color: {c}; background: transparent;"
        )
        self._clock.setStyleSheet(
            f"font-size: 48px; font-weight: 700;"
            f" font-family: 'SF Mono','Menlo',monospace;"
            f" letter-spacing: 2px; color: {c}; background: transparent;"
        )

    def _update_time(self) -> None:
        if not self.session:
            return
        started_at = timeutils.parse_iso(self.session.start_time)
        if started_at is None:
            self._clock.setText("--:--:--")
            self._tick.stop()
            return
        total = timeutils.duration_seconds(started_at, datetime.now())
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        self._clock.setText(f"{h:02d}:{m:02d}:{s:02d}")
