"""
Timer area widget — always visible.

Idle state: recent subject resume and today's tracked time.
Active state: live clock, elided subject name and stop button.
The stacked pages share a compact width and animate between fixed heights.
"""

from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QGraphicsOpacityEffect, QStackedWidget,
)
from PySide6.QtCore import QEasingCurve, QTimer, QVariantAnimation, Signal, Qt
from datetime import datetime
from ...core.models import Session, Subject
from ...core import timeutils
from ..accessibility import reduced_motion
from .labels import ElidedLabel


IDLE_MESSAGE = "Ready to track"
IDLE_HEIGHT = 64
ACTIVE_HEIGHT = 96


def _format_short_duration(seconds: int) -> str:
    h, rem = divmod(max(0, int(seconds)), 3600)
    m, _ = divmod(rem, 60)
    if h > 0:
        return f"{h}h {m:02d}m"
    return f"{m}m"


class ActiveTimerWidget(QFrame):
    """Always-visible timer area with slim idle bar and morphing active hero strip."""

    stop_requested = Signal()
    resume_requested = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.subject: Subject | None = None
        self.session: Session | None = None
        self._last_subject: Subject | None = None
        self._today_seconds: int = 0
        self._transient_notice: str = ""
        self._tokens: dict = {}

        self._tick = QTimer(self)
        self._tick.timeout.connect(self._update_time)

        self._notice_timer = QTimer(self)
        self._notice_timer.setSingleShot(True)
        self._notice_timer.timeout.connect(self._clear_transient_notice)

        self._height_anim = QVariantAnimation()
        self._height_anim.setDuration(160)
        self._height_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._height_anim.valueChanged.connect(self._apply_height_anim_value)
        self.destroyed.connect(self._height_anim.stop)

        self.setObjectName("timerArea")
        self.setFixedHeight(IDLE_HEIGHT)

        self._build_ui()
        self._show_idle()

    # ── UI construction ──────────────────────────────────────────────────
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self._stack = QStackedWidget()
        outer.addWidget(self._stack)

        # Page 0 — slim horizontal idle bar (D2-A)
        idle_page = QFrame()
        idle_page.setObjectName("idlePage")
        idle_lay = QHBoxLayout(idle_page)
        idle_lay.setContentsMargins(16, 10, 14, 10)
        idle_lay.setSpacing(10)

        self._idle_dot = QLabel()
        self._idle_dot.setFixedSize(8, 8)
        idle_lay.addWidget(self._idle_dot, 0, Qt.AlignVCenter)

        idle_text_col = QVBoxLayout()
        idle_text_col.setContentsMargins(0, 0, 0, 0)
        idle_text_col.setSpacing(1)

        self._idle_label = ElidedLabel(IDLE_MESSAGE)
        self._idle_label.setObjectName("idleMessage")
        self._idle_label.setStyleSheet(
            "font-size: 13px; font-weight: 650; background: transparent;"
        )
        idle_text_col.addWidget(self._idle_label)

        self._idle_hint = ElidedLabel("Click a subject or press 1–9 to start")
        self._idle_hint.setObjectName("idleHint")
        self._idle_hint.setStyleSheet(
            "font-size: 11px; font-weight: 500; background: transparent;"
        )
        idle_text_col.addWidget(self._idle_hint)
        idle_lay.addLayout(idle_text_col, 1)

        self._today_lbl = QLabel("")
        self._today_lbl.setObjectName("idleTodayStat")
        self._today_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._today_lbl.hide()
        idle_text_col.addWidget(self._today_lbl)

        self._resume_btn = QPushButton("↺ Resume")
        self._resume_btn.setObjectName("resumeBtn")
        self._resume_btn.setMinimumHeight(32)
        self._resume_btn.setCursor(Qt.PointingHandCursor)
        self._resume_btn.setFocusPolicy(Qt.StrongFocus)
        self._resume_btn.clicked.connect(self._on_resume_clicked)
        self._resume_btn.hide()
        idle_lay.addWidget(self._resume_btn, 0, Qt.AlignVCenter)

        self._stack.addWidget(idle_page)

        # Page 1 — horizontal active hero strip (D2-A)
        active_page = QFrame()
        active_page.setObjectName("activePage")
        active_lay = QHBoxLayout(active_page)
        active_lay.setContentsMargins(18, 12, 18, 12)
        active_lay.setSpacing(14)

        left_col = QVBoxLayout()
        left_col.setContentsMargins(0, 0, 0, 0)
        left_col.setSpacing(3)
        left_col.addStretch(1)

        header = ElidedLabel("CURRENTLY TRACKING")
        header.setObjectName("subtitle")
        header.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        left_col.addWidget(header)

        self._name = ElidedLabel()
        self._name.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._name.setStyleSheet("font-size: 18px; font-weight: 700; background: transparent;")
        left_col.addWidget(self._name)
        left_col.addStretch(1)

        active_lay.addLayout(left_col, 1)

        self._clock = QLabel("00:00:00")
        self._clock.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._clock.setStyleSheet(
            "font-size: 32px; font-weight: 700;"
            " font-family: 'SF Mono', 'Menlo', monospace;"
            " letter-spacing: 1.5px; background: transparent;"
        )
        active_lay.addWidget(self._clock, 0, Qt.AlignVCenter)

        self._stop_btn = QPushButton("■  Stop")
        self._stop_btn.setObjectName("stopBtn")
        self._stop_btn.setMinimumWidth(88)
        self._stop_btn.setMinimumHeight(40)
        self._stop_btn.setCursor(Qt.PointingHandCursor)
        self._stop_btn.setFocusPolicy(Qt.StrongFocus)
        self._stop_btn.setAccessibleName("Stop the active tracking session")
        self._stop_btn.clicked.connect(self.stop_requested.emit)
        active_lay.addWidget(self._stop_btn, 0, Qt.AlignVCenter)

        self._stack.addWidget(active_page)

    # ── Public API ───────────────────────────────────────────────────────
    def apply_tokens(self, tokens: dict) -> None:
        """Update colours to match current theme. Called after theme changes."""
        self._tokens = tokens
        if self.subject:
            self._apply_active_style()
        else:
            self._apply_idle_style()

    def set_idle_context(
        self,
        last_subject: Subject | None = None,
        today_seconds: int = 0,
    ) -> None:
        """Update the idle bar's 1-click Resume button and Today's tracked total."""
        self._last_subject = last_subject
        self._today_seconds = max(0, int(today_seconds))
        self._refresh_idle_controls()

    def show_transient_notice(self, message: str, duration_ms: int = 4000) -> None:
        """Briefly show a feedback notice (e.g. '<30s session discarded') in the idle bar."""
        self._transient_notice = message
        self._idle_label.setText(message)
        self._refresh_idle_controls()
        self._notice_timer.start(max(500, int(duration_ms)))

    def _clear_transient_notice(self) -> None:
        self._transient_notice = ""
        self._idle_label.setText(IDLE_MESSAGE)
        self._refresh_idle_controls()

    def _on_resume_clicked(self) -> None:
        if self._last_subject is not None and self._last_subject.id is not None:
            self.resume_requested.emit(int(self._last_subject.id))

    def _apply_height_anim_value(self, value) -> None:
        try:
            self.setFixedHeight(int(value))
        except RuntimeError:
            self._height_anim.stop()

    def _animate_height_to(self, target: int) -> None:
        self._height_anim.stop()
        if reduced_motion(self) or not self.isVisible():
            self.setFixedHeight(target)
            return
        if self.height() == target:
            return
        self._height_anim.setStartValue(self.height())
        self._height_anim.setEndValue(target)
        self._height_anim.start()

    def set_active(self, subject: Subject, session: Session) -> None:
        self._notice_timer.stop()
        self._transient_notice = ""
        self.subject = subject
        self.session = session
        self._last_subject = subject
        self._name.setText(subject.name)
        self._name.setToolTip(subject.name)
        self._name.setAccessibleName(f"Tracking subject: {subject.name}")
        self._clock.setAccessibleName("Elapsed tracking time")
        self._apply_active_style()
        self._update_time()
        self._tick.start(500)
        self._stack.setCurrentIndex(1)
        self._animate_height_to(ACTIVE_HEIGHT)

    def clear(self) -> None:
        """Switch to idle. Safe to call even when already idle."""
        self._clear_stop_pulse()
        if self.subject is None and not self._tick.isActive():
            self._refresh_idle_controls()
            return
        self._tick.stop()
        self.subject = None
        self.session = None
        self._show_idle()
        self._animate_height_to(IDLE_HEIGHT)

    def pulse_stop_button(self) -> None:
        """Draw attention to the only control allowed to end a session."""
        if self.subject is None:
            return
        if reduced_motion(self):
            self._stop_btn.setFocus(Qt.OtherFocusReason)
            return
        self._clear_stop_pulse()
        effect = QGraphicsOpacityEffect(self._stop_btn)
        effect.setOpacity(1.0)
        self._stop_btn.setGraphicsEffect(effect)
        if not hasattr(self, "_stop_pulse_anim"):
            # Retain one wrapper for this widget's lifetime. Deleting each
            # animation from its own Python finished closure formed a cycle
            # that could collide with deferred Qt destruction during GC.
            animation = QVariantAnimation(self)
            animation.setDuration(420)
            animation.setKeyValueAt(0.0, 1.0)
            animation.setKeyValueAt(0.25, 0.45)
            animation.setKeyValueAt(0.5, 1.0)
            animation.setKeyValueAt(0.75, 0.45)
            animation.setKeyValueAt(1.0, 1.0)
            animation.setEasingCurve(QEasingCurve.InOutCubic)
            animation.valueChanged.connect(self._update_stop_pulse)
            animation.finished.connect(self._clear_stop_pulse)
            self._stop_pulse_anim = animation
        self._stop_pulse_anim.start()

    def _update_stop_pulse(self, value) -> None:
        effect = self._stop_btn.graphicsEffect()
        if isinstance(effect, QGraphicsOpacityEffect):
            effect.setOpacity(float(value))

    def _clear_stop_pulse(self) -> None:
        animation = getattr(self, "_stop_pulse_anim", None)
        if animation is not None:
            animation.stop()
            self._stop_btn.setGraphicsEffect(None)

    # ── Private ──────────────────────────────────────────────────────────
    def _show_idle(self) -> None:
        self._idle_label.setText(self._transient_notice or IDLE_MESSAGE)
        self._refresh_idle_controls()
        self._apply_idle_style()
        self._stack.setCurrentIndex(0)

    def _refresh_idle_controls(self) -> None:
        self._idle_hint.setVisible(self._today_seconds == 0)
        if self._today_seconds > 0:
            self._today_lbl.setText(f"Today: {_format_short_duration(self._today_seconds)}")
            self._today_lbl.show()
        else:
            self._today_lbl.hide()

        subj = self._last_subject
        if subj is not None and subj.id is not None and not getattr(subj, "is_archived", False) and not self._transient_notice:
            short = subj.name if len(subj.name) <= 18 else subj.name[:17] + "…"
            self._resume_btn.setText(f"↺  Resume {short}")
            self._resume_btn.setToolTip(f"Resume tracking {subj.name}")
            self._resume_btn.setAccessibleName(f"Resume tracking {subj.name}")
            self._resume_btn.show()
        else:
            self._resume_btn.hide()
        self._apply_idle_style()

    def _apply_idle_style(self) -> None:
        t = self._tokens
        if not t:
            return
        idle_border = t.get("IDLE_BORDER", f"1px solid {t.get('BORDER_COLOR', '#1F2B42')}")
        radius = t.get("RADIUS", "10px")
        card_bg = t.get("CARD_BG", t["BG_SECONDARY"])
        self.setStyleSheet(f"""
            QFrame#timerArea {{
                background-color: {card_bg};
                border: {idle_border};
                border-radius: {radius};
            }}
        """)
        msg_color = t["ACCENT_RED"] if self._transient_notice else t["TEXT_PRIMARY"]
        dot_color = (
            t["ACCENT_RED"]
            if self._transient_notice
            else (self._last_subject.color if self._last_subject else t["TEXT_DIMMED"])
        )
        self._idle_dot.setStyleSheet(
            f"background-color: {dot_color}; border-radius: 4px; border: none;"
        )
        self._idle_label.setStyleSheet(
            f"font-size: 13px; font-weight: 650; color: {msg_color}; background: transparent;"
        )
        self._idle_hint.setStyleSheet(
            f"font-size: 11px; font-weight: 500; color: {t['TEXT_SECONDARY']}; background: transparent;"
        )
        self._today_lbl.setStyleSheet(
            f"font-size: 11px; font-weight: 600; color: {t['TEXT_SECONDARY']};"
            " background: transparent; border: none;"
        )
        accent = self._last_subject.color if self._last_subject else t["ACCENT"]
        self._resume_btn.setStyleSheet(
            f"QPushButton#resumeBtn {{ background: {t['BG_PRIMARY']}; border: 1.2px solid {accent};"
            f" border-radius: 7px; padding: 5px 11px; font-size: 11px; font-weight: 650; color: {t['TEXT_PRIMARY']}; }}"
            f"QPushButton#resumeBtn:hover {{ background: {t['BG_TERTIARY']}; border-color: {accent}; }}"
            f"QPushButton#resumeBtn:focus {{ border: 2px solid {t.get('BORDER_FOCUS', accent)}; }}"
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
        radius = t.get("RADIUS", "10px")
        self.setStyleSheet(f"""
            QFrame#timerArea {{
                background: {timer_bg};
                border: {timer_border};
                border-radius: {radius};
            }}
        """)
        self._name.setStyleSheet(
            f"font-size: 18px; font-weight: 700; color: {t['TEXT_PRIMARY']}; background: transparent;"
        )
        self._clock.setStyleSheet(
            f"font-size: 32px; font-weight: 700;"
            f" font-family: 'SF Mono','Menlo',monospace;"
            f" letter-spacing: 1.5px; color: {c}; background: transparent;"
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
