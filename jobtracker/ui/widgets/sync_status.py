"""Compact persistent sync feedback; bookkeeping belongs to the controller."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout


class SyncStatusWidget(QFrame):
    action_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("syncStatusPanel")
        self._status_key = None
        self._tokens = {}
        self._problem = False
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 5, 8, 5)
        row.setSpacing(8)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.label = QLabel("Local only · Sync off", self)
        self.label.setTextFormat(Qt.PlainText)
        self.label.setWordWrap(True)
        text.addWidget(self.label)
        self.error_label = QLabel(self)
        self.error_label.setTextFormat(Qt.PlainText)
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        text.addWidget(self.error_label)
        row.addLayout(text, 1)
        self.action = QPushButton("Settings", self)
        self.action.setMinimumHeight(36)
        self.action.setCursor(Qt.PointingHandCursor)
        self.action.setFocusPolicy(Qt.StrongFocus)
        self.action.clicked.connect(self.action_requested.emit)
        row.addWidget(self.action)

    def set_status(self, title, detail, *, action="Settings", busy=False, error=""):
        key = (title, detail, action, busy, error)
        if key == self._status_key:
            return
        self._status_key = key
        self._problem = bool(error)
        self.label.setText(title)
        self.label.setAccessibleName(f"Synchronization status: {title}")
        self.label.setAccessibleDescription(detail)
        self.error_label.setText(error[:180])
        self.error_label.setAccessibleName(f"Sync issue: {error}" if error else "")
        self.error_label.setVisible(bool(error))
        self.setToolTip(detail)
        self.setAccessibleName(f"Synchronization: {title}")
        self.setAccessibleDescription(detail)
        self.action.setText(action)
        self.action.setEnabled(not busy)
        self.action.setAccessibleName("Open sync settings" if action == "Settings" else "Retry synchronization" if action == "Retry" else "Synchronize now")
        self.action.setAccessibleDescription(detail)
        self.apply_tokens(self._tokens)

    def apply_tokens(self, tokens):
        self._tokens = tokens
        if not tokens:
            return
        foreground = tokens["ACCENT_RED"] if self._problem else tokens["TEXT_SECONDARY"]
        self.setStyleSheet(
            "QFrame#syncStatusPanel {"
            f"background: {tokens.get('CARD_BG', tokens['BG_SECONDARY'])};"
            f"border: 1px solid {tokens['BORDER_COLOR']}; border-radius: 6px; }}"
        )
        self.label.setStyleSheet(f"font-size: 12px; color: {foreground}; border: none; background: transparent;")
        self.error_label.setStyleSheet(f"font-size: 12px; color: {tokens['ACCENT_RED']}; border: none; background: transparent;")
