"""In-window modal panels that never create a second macOS window.

The content hosted here must be a :class:`InlineDialog`, not a reparented
``QDialog``.  On macOS, ``QDialog.done()`` can recreate the dialog's original
native ``NSWindow`` after it has been reparented as a child widget.  In native
fullscreen macOS then promotes that orphan into its own Space.  InlineDialog
provides the small result/signal API our editors need while remaining a
``QWidget`` for its entire lifetime.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QTimer, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

def dialog_owner(dialog: QWidget) -> QWidget | None:
    """Return the parent supplied by the caller before inline reparenting."""
    return getattr(dialog, "_jt_original_parent", None) or dialog.parentWidget()


class InlineDialog(QWidget):
    """Dialog-compatible content that is never a native/top-level window."""

    Accepted = QDialog.Accepted
    Rejected = QDialog.Rejected

    finished = Signal(int)
    accepted = Signal()
    rejected = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._jt_result = int(QDialog.Rejected)
        self._jt_finished = False
        self._jt_filter_installed = False
        self.setObjectName("jtInlineDialogContent")
        self.setAttribute(Qt.WA_StyledBackground, True)

    def result(self) -> int:
        return self._jt_result

    def setResult(self, result: int) -> None:  # noqa: N802
        self._jt_result = int(result)

    def accept(self) -> None:
        self.done(int(QDialog.Accepted))

    def reject(self) -> None:
        self.done(int(QDialog.Rejected))

    def done(self, result: int) -> None:
        if self._jt_finished:
            return
        self._jt_finished = True
        self._jt_result = int(result)
        self.hide()
        self._remove_key_filter()
        if result == int(QDialog.Accepted):
            self.accepted.emit()
        elif result == int(QDialog.Rejected):
            self.rejected.emit()
        self.finished.emit(int(result))

    def _install_key_filter(self) -> None:
        if self._jt_filter_installed:
            return
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
            self._jt_filter_installed = True

    def _remove_key_filter(self) -> None:
        if not self._jt_filter_installed:
            return
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self._jt_filter_installed = False

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if (
            event.type() not in (QEvent.KeyPress, QEvent.ShortcutOverride)
            or not isinstance(event, QKeyEvent)
            or not isinstance(watched, QWidget)
            or (watched is not self and not self.isAncestorOf(watched))
        ):
            return False
        if event.type() == QEvent.ShortcutOverride:
            if (
                self.claims_inline_key(event)
                or event.key() == Qt.Key_Escape
                or event.key() in (Qt.Key_Return, Qt.Key_Enter)
            ):
                # Prevent same-window MainWindow shortcuts from winning before
                # this inline editor receives its normal KeyPress event.
                event.accept()
                return True
            return False
        if self.handle_inline_key(event):
            return True
        if event.key() == Qt.Key_Escape:
            self.reject()
            return True
        if event.key() not in (Qt.Key_Return, Qt.Key_Enter):
            return False
        # Text editors own Return for line breaks. Single-line editors use the
        # same default-button behavior as QDialog.
        if isinstance(watched, QTextEdit):
            return False
        for button in self.findChildren(QPushButton):
            if button.isVisible() and button.isEnabled() and button.isDefault():
                button.click()
                return True
        return False

    def handle_inline_key(self, event: QKeyEvent) -> bool:
        """Let an editor consume keys before window-level app shortcuts."""
        return False

    def claims_inline_key(self, event: QKeyEvent) -> bool:
        """Return whether ShortcutOverride should reserve this key."""
        return False

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._jt_finished:
            event.accept()
            return
        event.ignore()
        self.reject()

    def deleteLater(self) -> None:  # noqa: N802
        self._remove_key_filter()
        super().deleteLater()


class _InlineDialogLayer(QWidget):
    """A modal-looking panel hosted inside the existing top-level window."""

    def __init__(self, host: QWidget, dialog: InlineDialog) -> None:
        super().__init__(host)
        self._host = host
        self.dialog = dialog
        self.setObjectName("jtInlineDialogLayer")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            "QWidget#jtInlineDialogLayer { background-color: rgba(0, 0, 0, 112); }"
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.addStretch(1)

        panel = QFrame(self)
        panel.setObjectName("jtInlineDialogPanel")
        tokens = getattr(dialog_owner(dialog), "_tokens", {}) or {}
        panel_bg = tokens.get("BG_SECONDARY", "#171B24")
        border = tokens.get("BORDER_COLOR", "#374151")
        text = tokens.get("TEXT_PRIMARY", "#F9FAFB")
        panel.setStyleSheet(
            "QFrame#jtInlineDialogPanel {"
            f" background-color: {panel_bg}; border: 1px solid {border};"
            " border-radius: 12px;"
            "}"
            "QLabel#jtInlineDialogTitle {"
            f" color: {text}; font-size: 15px; font-weight: 700;"
            " background: transparent; border: none;"
            "}"
            "QPushButton#jtInlineDialogClose {"
            f" color: {text}; background: transparent; border: none;"
            " font-size: 20px; padding: 2px 8px;"
            "}"
        )
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(1, 8, 1, 1)
        panel_layout.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(16, 0, 8, 4)
        title = QLabel(dialog.windowTitle() or "JobTracker")
        title.setObjectName("jtInlineDialogTitle")
        header.addWidget(title)
        header.addStretch(1)
        close = QPushButton("×")
        close.setObjectName("jtInlineDialogClose")
        close.setFixedSize(32, 30)
        close.setCursor(Qt.PointingHandCursor)
        close.clicked.connect(dialog.reject)
        header.addWidget(close)
        panel_layout.addLayout(header)

        # InlineDialog has been a child QWidget since its constructor. This
        # reparent only moves it within the existing widget tree; no QWindow or
        # NSWindow exists to orphan.
        dialog.setParent(panel)
        panel_layout.addWidget(dialog)

        outer.addWidget(panel, 0, Qt.AlignHCenter)
        outer.addStretch(1)
        self._panel = panel

        host.installEventFilter(self)
        count = int(host.property("_jt_inline_dialog_count") or 0)
        host.setProperty("_jt_inline_dialog_count", count + 1)
        self._host_released = False
        self.destroyed.connect(lambda: self._release_host())

    def _release_host(self) -> None:
        if self._host_released:
            return
        self._host_released = True
        try:
            self._host.removeEventFilter(self)
            count = int(self._host.property("_jt_inline_dialog_count") or 1)
            self._host.setProperty("_jt_inline_dialog_count", max(0, count - 1))
        except RuntimeError:
            pass

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched is self._host and event.type() == QEvent.Resize:
            self.setGeometry(self._host.rect())
        return False

    def show_dialog(self) -> None:
        self.setGeometry(self._host.rect())
        self.show()
        self.raise_()
        self.dialog.show()
        self.dialog.raise_()
        self.dialog._install_key_filter()
        self.dialog.focusNextChild()


class _InlineMessageDialog(InlineDialog):
    """Small QMessageBox replacement that remains an ordinary child widget."""

    def __init__(
        self,
        parent: QWidget,
        icon: QMessageBox.Icon,
        title: str,
        text: str,
        buttons: QMessageBox.StandardButton,
        default_button: QMessageBox.StandardButton,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 18, 24, 20)
        layout.setSpacing(14)

        content = QHBoxLayout()
        symbols = {
            QMessageBox.Information: "ℹ",
            QMessageBox.Warning: "⚠",
            QMessageBox.Critical: "⛔",
            QMessageBox.Question: "?",
        }
        symbol = QLabel(symbols.get(icon, ""))
        symbol.setStyleSheet(
            "font-size: 24px; font-weight: 700; background: transparent;"
        )
        content.addWidget(symbol, 0, Qt.AlignTop)
        message = QLabel(text)
        message.setWordWrap(True)
        message.setMinimumWidth(260)
        message.setStyleSheet("background: transparent;")
        content.addWidget(message, 1)
        layout.addLayout(content)

        row = QHBoxLayout()
        row.addStretch(1)
        labels = (
            (QMessageBox.Cancel, "Cancel"),
            (QMessageBox.No, "No"),
            (QMessageBox.Yes, "Yes"),
            (QMessageBox.Ok, "OK"),
        )
        made = []
        for standard_button, label in labels:
            if not (buttons & standard_button):
                continue
            button = QPushButton(label)
            button.clicked.connect(
                lambda _checked=False, result=standard_button: self.done(
                    int(result)
                )
            )
            row.addWidget(button)
            made.append((standard_button, button))
        layout.addLayout(row)

        selected_default = default_button
        if selected_default == QMessageBox.NoButton:
            selected_default = QMessageBox.Ok if buttons & QMessageBox.Ok else QMessageBox.No
        for standard_button, button in made:
            is_default = standard_button == selected_default
            button.setDefault(is_default)
            button.setAutoDefault(is_default)


def configure_window_modal(dialog: QWidget) -> None:
    """Configure genuine Qt dialogs as parent-window-modal fallbacks."""
    if isinstance(dialog, QDialog) and dialog.parentWidget() is not None:
        dialog.setWindowModality(Qt.WindowModal)


def open_dialog(
    dialog: InlineDialog | QDialog,
    on_finished: Callable[[int, QWidget], None] | None = None,
) -> InlineDialog | QDialog:
    """Open modal content inside the existing fullscreen window.

    No secondary native window is created, so macOS has no window/Space
    activation to animate. Opening and result processing are also deferred
    outside their originating input events.
    """
    original_parent = dialog.parentWidget()
    dialog._jt_original_parent = original_parent
    layer = None
    if original_parent is not None and isinstance(dialog, InlineDialog):
        host = original_parent.window()
        layer = _InlineDialogLayer(host, dialog)
        dialog._jt_inline_layer = layer

    def queue_finished(result: int) -> None:
        def finish() -> None:
            try:
                if on_finished is not None:
                    on_finished(result, dialog)
            finally:
                if layer is not None:
                    layer.hide()
                    layer._release_host()
                    layer.deleteLater()
                dialog.deleteLater()

        QTimer.singleShot(0, finish)

    dialog.finished.connect(queue_finished)
    if layer is not None:
        QTimer.singleShot(0, layer.show_dialog)
    else:
        # Framework dialogs such as QColorDialog and QFileDialog remain real
        # window-modal dialogs. Crucially, they are never reparented after
        # construction, which is the transition that orphaned NSWindows.
        QTimer.singleShot(0, dialog.open)
    return dialog


def _message_box(
    parent: QWidget,
    icon: QMessageBox.Icon,
    title: str,
    text: str,
    buttons: QMessageBox.StandardButton = QMessageBox.Ok,
    default_button: QMessageBox.StandardButton = QMessageBox.NoButton,
    on_finished: Callable[[QMessageBox.StandardButton], None] | None = None,
) -> InlineDialog:
    box = _InlineMessageDialog(
        parent, icon, title, text, buttons, default_button
    )
    configure_window_modal(box)

    def finish(result: int, _dialog: QWidget) -> None:
        if on_finished is not None:
            on_finished(QMessageBox.StandardButton(result))

    return open_dialog(box, finish)


def information(
    parent: QWidget,
    title: str,
    text: str,
    buttons: QMessageBox.StandardButton = QMessageBox.Ok,
    default_button: QMessageBox.StandardButton = QMessageBox.NoButton,
    on_finished: Callable[[QMessageBox.StandardButton], None] | None = None,
) -> InlineDialog:
    return _message_box(
        parent,
        QMessageBox.Information,
        title,
        text,
        buttons,
        default_button,
        on_finished,
    )


def warning(
    parent: QWidget,
    title: str,
    text: str,
    buttons: QMessageBox.StandardButton = QMessageBox.Ok,
    default_button: QMessageBox.StandardButton = QMessageBox.NoButton,
    on_finished: Callable[[QMessageBox.StandardButton], None] | None = None,
) -> InlineDialog:
    return _message_box(
        parent,
        QMessageBox.Warning,
        title,
        text,
        buttons,
        default_button,
        on_finished,
    )


def critical(
    parent: QWidget,
    title: str,
    text: str,
    buttons: QMessageBox.StandardButton = QMessageBox.Ok,
    default_button: QMessageBox.StandardButton = QMessageBox.NoButton,
    on_finished: Callable[[QMessageBox.StandardButton], None] | None = None,
) -> InlineDialog:
    return _message_box(
        parent,
        QMessageBox.Critical,
        title,
        text,
        buttons,
        default_button,
        on_finished,
    )


def question(
    parent: QWidget,
    title: str,
    text: str,
    on_finished: Callable[[QMessageBox.StandardButton], None],
    buttons: QMessageBox.StandardButton = QMessageBox.Yes | QMessageBox.No,
    default_button: QMessageBox.StandardButton = QMessageBox.NoButton,
) -> InlineDialog:
    return _message_box(
        parent,
        QMessageBox.Question,
        title,
        text,
        buttons,
        default_button,
        on_finished,
    )
