"""
Settings dialog — appearance, preferences, and data management.
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QMessageBox, QFileDialog, QComboBox,
    QCheckBox, QLineEdit,
    QScrollArea, QWidget,
    QApplication,
)
from PySide6.QtCore import QEvent, QPointF, Qt, QTimer
from PySide6.QtGui import QPainter, QPalette, QPen, QPolygonF
import json
import logging
import zipfile
from typing import Optional

from ...services.tracker_service import TrackerService
from ...core.themes import THEME_DESCRIPTIONS, THEME_PRESETS, get_tokens
from ...core import export_bundle
from ...core.timeutils import parse_day_start
from ...sync import settings as sync_settings
from .dialog_utils import (
    InlineDialog,
    configure_window_modal,
    critical,
    information,
    open_dialog,
    question,
)

logger = logging.getLogger("jobtracker")

class _SettingsComboBox(QComboBox):
    """Keep the disclosure legible and sharp on dark and Retina surfaces."""

    def __init__(self) -> None:
        super().__init__()
        self.setStyleSheet("QComboBox::down-arrow { image: none; }")

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(self.palette().color(QPalette.Text), 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        x, y = self.width() - 17, self.height() / 2
        painter.drawPolyline(QPolygonF([QPointF(x - 4, y - 2), QPointF(x, y + 2), QPointF(x + 4, y - 2)]))


class SettingsDialog(InlineDialog):
    def __init__(self, parent=None, service=None) -> None:
        super().__init__(parent)
        configure_window_modal(self)
        # All persistence goes through the service (not the db directly).
        self._svc = service or getattr(parent, "service", None) or TrackerService()
        self.setWindowTitle("Settings")
        self.setFixedWidth(440)
        self._settings_host = parent.window() if parent is not None else None
        self._fit_owner_height()
        if self._settings_host is not None:
            self._settings_host.installEventFilter(self)

        # Load current prefs
        self._fx = self._svc.get_setting("theme_fx", "Glow")
        self._palette = self._svc.get_setting("theme_palette", "Ocean")
        self._day_start = self._svc.get_setting("day_start_time", "03:00")
        self._original_fx = self._fx
        self._original_palette = self._palette

        self._build_ui()
        self._settings_app = QApplication.instance()
        if self._settings_app is not None:
            self._settings_app.focusChanged.connect(self._keep_focus_visible)

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setObjectName("settingsScroll")
        self.scroll_area.setFrameShape(QFrame.NoFrame)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setAccessibleName("Settings options")
        content = QWidget(self.scroll_area)
        content.setObjectName("settingsScrollContents")
        layout = QVBoxLayout(content)
        self.scroll_area.setWidget(content)
        outer.addWidget(self.scroll_area)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)

        theme_label = QLabel("Theme")
        theme_label.setStyleSheet("font-size: 14px; font-weight: 700;")
        layout.addWidget(theme_label)
        self.theme_combo = _SettingsComboBox()
        self.theme_combo.setAccessibleName("Appearance theme")
        self.theme_combo.setAccessibleDescription("Choose one complete theme. Changes preview immediately; Save keeps them and Cancel restores your previous appearance.")
        self.theme_combo.setMinimumHeight(38)
        self.theme_combo.setCursor(Qt.PointingHandCursor)
        theme_label.setBuddy(self.theme_combo)
        for name, combination in THEME_PRESETS.items():
            self.theme_combo.addItem(name, combination)
        # Legacy combinations remain valid preferences. Represent the exact
        # saved pair without silently replacing it with the closest new theme.
        if (self._fx, self._palette) not in THEME_PRESETS.values():
            self.theme_combo.insertItem(0, "Current appearance (kept)", (self._fx, self._palette))
        self.theme_combo.currentIndexChanged.connect(self._select_theme)
        layout.addWidget(self.theme_combo)
        self._theme_hint = QLabel()
        self._theme_hint.setWordWrap(True)
        self._theme_hint.setStyleSheet("font-size: 12px;")
        layout.addWidget(self._theme_hint)

        self.reduce_motion_check = QCheckBox("Reduce motion")
        self.reduce_motion_check.setChecked(self._svc.get_setting("ui_reduce_motion", "0") == "1")
        motion_hint = "Pause animated backgrounds and decorative feedback. Timer clocks keep updating."
        self.reduce_motion_check.setToolTip(motion_hint)
        self.reduce_motion_check.setAccessibleDescription(motion_hint)
        layout.addWidget(self.reduce_motion_check)

        self._refresh_theme_selection()

        # ── Separator ────────────────────────────────────────────────────
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background-color: #303040;")
        layout.addWidget(sep)

        # ── Logical day start ────────────────────────────────────────────
        day_lbl = QLabel("Day starts at")
        day_lbl.setStyleSheet("font-size: 14px; font-weight: 700;")
        layout.addWidget(day_lbl)

        day_row = QHBoxLayout()
        self.day_start_combo = _SettingsComboBox()
        self.day_start_combo.setCursor(Qt.PointingHandCursor)
        for h in range(24):
            self.day_start_combo.addItem(f"{h:02d}:00", h)
        configured_start = parse_day_start(self._day_start)
        if configured_start.minute:
            value = configured_start.strftime("%H:%M")
            self.day_start_combo.insertItem(configured_start.hour + 1, value, value)
            self.day_start_combo.setCurrentIndex(configured_start.hour + 1)
        else:
            self.day_start_combo.setCurrentIndex(configured_start.hour)
        self.day_start_combo.setMinimumHeight(32)
        self.day_start_combo.setAccessibleName("Tracking day start")
        day_lbl.setBuddy(self.day_start_combo)
        day_row.addWidget(self.day_start_combo)
        day_hint = QLabel("late-night work counts on the day it started")
        day_hint.setStyleSheet("font-size: 11px; opacity: 0.7;")
        day_hint.setWordWrap(True)
        day_row.addWidget(day_hint, 1)
        layout.addLayout(day_row)

        # ── Data Management ──────────────────────────────────────────────
        data_lbl = QLabel("Data")
        data_lbl.setStyleSheet("font-size: 14px; font-weight: 700;")
        layout.addWidget(data_lbl)

        data_row = QHBoxLayout()
        data_row.setSpacing(10)

        export_btn = QPushButton("Export Backup")
        export_btn.setCursor(Qt.PointingHandCursor)
        export_btn.setMinimumHeight(36)
        export_btn.clicked.connect(self._export)
        data_row.addWidget(export_btn)

        import_btn = QPushButton("Import Backup")
        import_btn.setCursor(Qt.PointingHandCursor)
        import_btn.setMinimumHeight(36)
        import_btn.setToolTip(
            "Choose the exported .zip bundle, or a standalone JobTracker JSON backup"
        )
        import_btn.clicked.connect(self._import)
        data_row.addWidget(import_btn)

        layout.addLayout(data_row)

        # ── Sync with your server ────────────────────────────────────────
        sync_lbl = QLabel("Sync")
        sync_lbl.setStyleSheet("font-size: 14px; font-weight: 700;")
        layout.addWidget(sync_lbl)

        self.sync_enabled_check = QCheckBox("Sync with my server")
        self.sync_enabled_check.setCursor(Qt.PointingHandCursor)
        self.sync_enabled_check.setChecked(sync_settings.is_enabled(self._svc))
        self.sync_enabled_check.setToolTip(
            "Off means the app is purely local, exactly as before."
        )
        layout.addWidget(self.sync_enabled_check)

        self.sync_url_input = QLineEdit(sync_settings.server_url(self._svc))
        self.sync_url_input.setPlaceholderText("https://your-server:8443")
        self.sync_url_input.setMinimumHeight(32)
        self.sync_url_input.setAccessibleName("Sync server address")
        layout.addWidget(self.sync_url_input)

        self.sync_token_input = QLineEdit()
        self.sync_token_input.setEchoMode(QLineEdit.Password)
        self.sync_token_input.setMinimumHeight(32)
        self.sync_token_input.setAccessibleName("Sync access token")
        self.sync_token_input.setAccessibleDescription("Leave blank to keep the existing token. The stored token is never displayed.")
        # Never render the stored token back into the field. Show that one
        # exists, and let an empty field mean "leave it alone".
        self.sync_token_input.setPlaceholderText(
            "Access token — leave blank to keep the current one"
            if sync_settings.read_token()
            else "Access token"
        )
        layout.addWidget(self.sync_token_input)

        sync_row = QHBoxLayout()
        sync_row.setSpacing(10)
        self.sync_now_btn = QPushButton("Sync Now")
        self.sync_now_btn.setCursor(Qt.PointingHandCursor)
        self.sync_now_btn.setMinimumHeight(36)
        self.sync_now_btn.clicked.connect(self._sync_now)
        sync_row.addWidget(self.sync_now_btn)

        self.sync_status_lbl = QLabel(self._sync_status_text())
        self.sync_status_lbl.setStyleSheet("font-size: 11px; opacity: 0.7;")
        self.sync_status_lbl.setWordWrap(True)
        sync_row.addWidget(self.sync_status_lbl, 1)
        layout.addLayout(sync_row)

        # Keep the status line live while the dialog is open. A one-shot check
        # after the button was pressed left "Syncing…" on screen for good if the
        # pass took longer than the delay, and the only way to see the result was
        # to close and reopen Settings. The timer is parented to the dialog, so
        # it stops when the dialog goes.
        self._sync_status_timer = QTimer(self)
        self._sync_status_timer.setInterval(600)
        self._sync_status_timer.timeout.connect(self._refresh_sync_status)
        self._sync_status_timer.start()

        layout.addStretch()

        # ── Actions ──────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(24, 0, 24, 18)
        btn_row.setSpacing(10)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setMinimumHeight(36)
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.clicked.connect(self._cancel)
        btn_row.addWidget(cancel_btn)

        save_btn = QPushButton("Save")
        save_btn.setObjectName("primaryBtn")
        save_btn.setMinimumHeight(36)
        save_btn.setCursor(Qt.PointingHandCursor)
        save_btn.clicked.connect(self.accept)
        btn_row.addWidget(save_btn)

        outer.addLayout(btn_row)

        for b in self.findChildren(QPushButton):
            b.setAutoDefault(False)
            b.setDefault(False)
        save_btn.setAutoDefault(True)
        save_btn.setDefault(True)

    def _fit_owner_height(self) -> None:
        available = self._settings_host.height() - 110 if self._settings_host is not None else 640
        self.setFixedHeight(max(240, min(640, available)))

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched is self._settings_host and event.type() == QEvent.Resize:
            self._fit_owner_height()
            QTimer.singleShot(0, self, self._keep_focus_visible)
        return super().eventFilter(watched, event)

    def _keep_focus_visible(self, _previous=None, current=None) -> None:
        current = current or QApplication.focusWidget()
        if self.isVisible() and current is not None and self.scroll_area.isAncestorOf(current):
            # ensureWidgetVisible uses a text field's cursor rectangle, which
            # can leave its lower border clipped. Reveal the whole control.
            center = current.mapTo(self.scroll_area.widget(), current.rect().center())
            self.scroll_area.ensureVisible(center.x(), center.y(), 0, (current.height() + 1) // 2 + 12)

    # ── Selection ────────────────────────────────────────────────────────
    def _select_preset(self, name: str) -> None:
        self.theme_combo.setCurrentIndex(self.theme_combo.findText(name))

    def _select_theme(self, index: int) -> None:
        combination = self.theme_combo.itemData(index)
        if combination is not None:
            self._fx, self._palette = combination
            self._apply_preview()

    def _refresh_theme_selection(self) -> None:
        self.theme_combo.blockSignals(True)
        for index in range(self.theme_combo.count()):
            if tuple(self.theme_combo.itemData(index)) == (self._fx, self._palette):
                self.theme_combo.setCurrentIndex(index)
                break
        self.theme_combo.blockSignals(False)
        self._theme_hint.setText(THEME_DESCRIPTIONS.get(
            self.theme_combo.currentText(),
            "Your saved appearance stays in place until you choose another theme.",
        ))

    def _apply_preview(self) -> None:
        """Live-preview the theme on the main window."""
        from ..styles import build_stylesheet

        tokens = get_tokens(self._fx, self._palette)
        main = self._resolve_main_window()
        if main and hasattr(main, "app_instance"):
            main.app_instance.setStyleSheet(build_stylesheet(tokens))
        if main and hasattr(main, "_timer"):
            main._timer.apply_tokens(tokens)
        if main and hasattr(main, "_fx_bg"):
            main._fx_bg.apply_theme(tokens, self._fx)
        if main and hasattr(main, "_graph_view"):
            main._graph_view.set_tokens(tokens)
        if main and hasattr(main, "_agenda_view"):
            main._agenda_view.set_tokens(tokens)
        layer = getattr(self, "_jt_inline_layer", None)
        if layer is not None:
            layer.apply_tokens(tokens)
        self._preview_navigation_and_weight(main, tokens)
        self._refresh_theme_selection()

    def _preview_navigation_and_weight(self, main, tokens) -> None:
        if main and hasattr(main, "_weight_view"):
            main._weight_view.set_tokens(tokens)
        if main and hasattr(main, "_sync_status"):
            main._sync_status.apply_tokens(tokens)
        for button in getattr(main, "_nav_buttons", []):
            if hasattr(button, "set_tokens"):
                button.set_tokens(tokens)

    def _cancel(self) -> None:
        self.reject()

    def done(self, result: int) -> None:
        if self._jt_finished:
            return
        if result == QDialog.Rejected:
            self._restore_preview()
        self._sync_status_timer.stop()
        if self._settings_app is not None:
            self._settings_app.focusChanged.disconnect(self._keep_focus_visible)
        if self._settings_host is not None:
            self._settings_host.removeEventFilter(self)
        super().done(result)

    def _restore_preview(self) -> None:
        """Cancel, Escape and the panel close button share one rollback."""
        if self._fx != self._original_fx or self._palette != self._original_palette:
            from ..styles import build_stylesheet

            tokens = get_tokens(self._original_fx, self._original_palette)
            main = self._resolve_main_window()
            if main and hasattr(main, "app_instance"):
                main.app_instance.setStyleSheet(build_stylesheet(tokens))
            if main and hasattr(main, "_timer"):
                main._timer.apply_tokens(tokens)
            if main and hasattr(main, "_fx_bg"):
                main._fx_bg.apply_theme(tokens, self._original_fx)
            if main and hasattr(main, "_graph_view"):
                main._graph_view.set_tokens(tokens)
            if main and hasattr(main, "_agenda_view"):
                main._agenda_view.set_tokens(tokens)
            layer = getattr(self, "_jt_inline_layer", None)
            if layer is not None:
                layer.apply_tokens(tokens)
            self._preview_navigation_and_weight(main, tokens)
        self._fx = self._original_fx
        self._palette = self._original_palette

    def _resolve_main_window(self) -> Optional[object]:
        current = self.parent()
        while current is not None:
            if hasattr(current, "app_instance"):
                return current
            current = current.parent()
        return None

    # ── Data ─────────────────────────────────────────────────────────────
    def _export(self) -> None:
        """Export a bundle .zip: authoritative JSON + readable CSV files."""
        dialog = QFileDialog(
            self,
            "Export Backup Bundle",
            "",
            "Zip bundle (*.zip)",
        )
        dialog.setAcceptMode(QFileDialog.AcceptSave)
        dialog.setFileMode(QFileDialog.AnyFile)
        dialog.setDefaultSuffix("zip")
        dialog.selectFile("jobtracker_backup.zip")
        dialog.setOption(QFileDialog.DontUseNativeDialog, True)
        configure_window_modal(dialog)
        open_dialog(dialog, self._finish_export_selection)

    def _finish_export_selection(
        self, result: int, dialog: QFileDialog
    ) -> None:
        if result != QDialog.Accepted or not dialog.selectedFiles():
            return
        path = dialog.selectedFiles()[0]
        try:
            export_data = self._svc.export_data()
            # Daily summary CSV respects the logical day-start setting.
            breakdown = self._svc.get_subject_breakdown(grouping="daily", days=None)
            export_bundle.write_zip(path, export_data, breakdown)
            information(
                self, "Exported",
                "Backup bundle saved:\n• jobtracker_backup.json (restore file)\n"
                "• sessions.csv, subjects.csv, daily_summary.csv",
            )
        except Exception as exc:
            logger.exception("Export failed")
            critical(self, "Error", f"Export failed:\n{exc}")

    def _import(self) -> None:
        dialog = QFileDialog(
            self,
            "Import Backup Bundle",
            "",
            "JobTracker backup bundle (*.zip *.json)",
        )
        dialog.setAcceptMode(QFileDialog.AcceptOpen)
        dialog.setFileMode(QFileDialog.ExistingFile)
        dialog.setOption(QFileDialog.DontUseNativeDialog, True)
        configure_window_modal(dialog)
        open_dialog(dialog, self._finish_import_selection)

    def _finish_import_selection(
        self, result: int, dialog: QFileDialog
    ) -> None:
        if result != QDialog.Accepted or not dialog.selectedFiles():
            return
        path = dialog.selectedFiles()[0]
        question(
            self, "Import Data",
            "This will merge the backup into your existing data.\nProceed?",
            lambda answer: self._finish_import(path, answer),
        )

    def _finish_import(
        self, path: str, answer: QMessageBox.StandardButton
    ) -> None:
        if answer != QMessageBox.Yes:
            return
        try:
            if path.lower().endswith(".zip"):
                with zipfile.ZipFile(path) as zf:
                    data = json.loads(zf.read(export_bundle.JSON_FILENAME))
            else:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
            self._svc.import_data(data)
            # Imported preferences are the new baseline, so closing Settings
            # cannot restore an earlier unsaved preview over the import.
            self._original_fx = self._fx = self._svc.get_setting("theme_fx", self._fx)
            self._original_palette = self._palette = self._svc.get_setting("theme_palette", self._palette)
            main = self._resolve_main_window()
            if main is not None and hasattr(main, "_reload"):
                # Apply restored preferences immediately. Close this settings
                # dialog afterward so its pre-import controls cannot overwrite
                # the restored values when Save is pressed.
                if hasattr(main, "_fx"):
                    main._fx = self._svc.get_setting("theme_fx", main._fx)
                if hasattr(main, "_palette"):
                    main._palette = self._svc.get_setting("theme_palette", main._palette)
                if hasattr(main, "_apply_theme"):
                    main._apply_theme()
                main._reload()
            information(
                self,
                "Imported",
                "Data restored successfully.",
                on_finished=lambda _answer: self.reject(),
            )
        except Exception as exc:
            logger.exception("Import failed")
            critical(self, "Error", f"Import failed:\n{exc}")

    # ── Sync ─────────────────────────────────────────────────────────────
    def _sync_status_text(self) -> str:
        """One line describing where sync stands, without leaking the token."""
        blocked = sync_settings.describe(self._svc)
        if blocked:
            return blocked
        main = self._resolve_main_window()
        controller = getattr(main, "_sync_controller", None) if main else None
        if controller is None:
            return "Enabled — restart to connect"
        if controller.busy:
            return "Syncing…"
        result = controller.last_result
        return result.summary() if result is not None else "Ready"

    def _sync_now(self) -> None:
        """Save what is in the fields first, so the button uses them right away."""
        self._persist_sync_settings()
        main = self._resolve_main_window()
        if main is None or not hasattr(main, "sync_now"):
            self.sync_status_lbl.setText("Restart the app to start syncing")
            return
        if getattr(main, "_sync_controller", None) is None:
            self.sync_status_lbl.setText("Restart the app to start syncing")
            return
        started = main.sync_now()
        self.sync_status_lbl.setText("Syncing…" if started else "A sync is already running")
        # The polling timer takes it from here and shows the real outcome.

    def _refresh_sync_status(self) -> None:
        try:
            self.sync_status_lbl.setText(self._sync_status_text())
        except RuntimeError:
            pass  # dialog closed while the timer was pending

    def _persist_sync_settings(self) -> None:
        sync_settings.set_enabled(self._svc, self.sync_enabled_check.isChecked())
        sync_settings.set_server_url(self._svc, self.sync_url_input.text())
        typed = self.sync_token_input.text().strip()
        # An empty field means "keep the token I already have", so a user editing
        # the server address does not silently wipe their credentials.
        if typed:
            sync_settings.write_token(typed)

    # ── Result ───────────────────────────────────────────────────────────
    def get_settings(self) -> dict:
        hour = self.day_start_combo.currentData()
        self._persist_sync_settings()
        return {
            "theme_fx": self._fx,
            "theme_palette": self._palette,
            "day_start_time": hour if isinstance(hour, str) else f"{int(hour):02d}:00",
            "ui_reduce_motion": self.reduce_motion_check.isChecked(),
        }
