"""
Settings dialog — theme FX, colour palette, and data management.
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QPushButton, QFrame, QMessageBox, QFileDialog, QComboBox,
    QCheckBox, QLineEdit,
    QScrollArea, QWidget,
    QApplication,
)
from PySide6.QtCore import QEvent, Qt, QTimer
import json
import logging
import zipfile
from typing import Optional

from ...services.tracker_service import TrackerService
from ...core.themes import PALETTES, PALETTE_NAMES, FX_NAMES, get_tokens
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

THEME_PRESETS = {
    "OLED Midnight": ("Clean", "Obsidian"),
    "Carbon Dark": ("Clean", "Dark"),
}


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

        theme_label = QLabel("Dark themes")
        theme_label.setStyleSheet("font-size: 14px; font-weight: 700;")
        layout.addWidget(theme_label)
        presets = QHBoxLayout()
        self._preset_btns = {}
        for name in THEME_PRESETS:
            button = QPushButton(name)
            button.setCheckable(True)
            button.setMinimumHeight(54)
            button.setCursor(Qt.PointingHandCursor)
            button.setAccessibleName(f"Use {name} theme")
            button.setToolTip("Layered dark surfaces with the Clean effect")
            button.clicked.connect(lambda _checked=False, n=name: self._select_preset(n))
            presets.addWidget(button, 1)
            self._preset_btns[name] = button
        layout.addLayout(presets)

        # ── FX Style ─────────────────────────────────────────────────────
        fx_lbl = QLabel("Theme FX")
        fx_lbl.setStyleSheet("font-size: 14px; font-weight: 700;")
        layout.addWidget(fx_lbl)

        fx_row = QHBoxLayout()
        fx_row.setSpacing(8)
        self._fx_btns: list[QPushButton] = []
        fx_labels = {
            "Base": "Base",
            "Clean": "Clean",
            "Glow": "Aura",
            "Space": "Space",
            "Checkerboard": "Minimal",
        }
        for name in FX_NAMES:
            btn = QPushButton(fx_labels.get(name, name))
            btn.setAccessibleName(f"Theme effect {fx_labels.get(name, name)}")
            btn.setFixedHeight(42)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setProperty("fx_name", name)
            btn.clicked.connect(lambda checked, n=name: self._select_fx(n))
            fx_row.addWidget(btn)
            self._fx_btns.append(btn)
        layout.addLayout(fx_row)

        # ── Colour Palette ───────────────────────────────────────────────
        palette_lbl = QLabel("Colour Palette")
        palette_lbl.setStyleSheet("font-size: 14px; font-weight: 700;")
        layout.addWidget(palette_lbl)

        palette_grid = QGridLayout()
        palette_grid.setContentsMargins(0, 2, 0, 2)
        palette_grid.setHorizontalSpacing(8)
        palette_grid.setVerticalSpacing(12)
        self._palette_btns: list[QPushButton] = []
        max_cols = 6
        for idx, name in enumerate(PALETTE_NAMES):
            btn = QPushButton()
            btn.setFixedSize(48, 48)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(name)
            btn.setAccessibleName(f"Color palette {name}")
            btn.setProperty("palette_name", name)
            btn.clicked.connect(lambda checked, n=name: self._select_palette(n))
            row, col = divmod(idx, max_cols)
            palette_grid.addWidget(btn, row, col)
            self._palette_btns.append(btn)
        for row in range((len(PALETTE_NAMES) + max_cols - 1) // max_cols):
            palette_grid.setRowMinimumHeight(row, 56)
        layout.addLayout(palette_grid)

        self._palette_name_lbl = QLabel(self._palette)
        self._palette_name_lbl.setStyleSheet("font-size: 12px; font-weight: 500;")
        layout.addWidget(self._palette_name_lbl)

        self.reduce_motion_check = QCheckBox("Reduce motion")
        self.reduce_motion_check.setChecked(self._svc.get_setting("ui_reduce_motion", "0") == "1")
        motion_hint = "Pause animated backgrounds and decorative feedback. Timer clocks keep updating."
        self.reduce_motion_check.setToolTip(motion_hint)
        self.reduce_motion_check.setAccessibleDescription(motion_hint)
        layout.addWidget(self.reduce_motion_check)

        self._refresh_buttons()
        QTimer.singleShot(0, self._refresh_buttons)

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
        self.day_start_combo = QComboBox()
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

        layout.addLayout(btn_row)

        for b in self.findChildren(QPushButton):
            b.setAutoDefault(False)
            b.setDefault(False)
        save_btn.setAutoDefault(True)
        save_btn.setDefault(True)

    def _fit_owner_height(self) -> None:
        available = self._settings_host.height() - 110 if self._settings_host is not None else 780
        self.setFixedHeight(max(240, min(780, available)))

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
        self._fx, self._palette = THEME_PRESETS[name]
        self._palette_name_lbl.setText(self._palette)
        self._apply_preview()

    def _select_fx(self, name: str) -> None:
        self._fx = name
        self._refresh_buttons()
        self._apply_preview()

    def _select_palette(self, name: str) -> None:
        self._palette = name
        self._palette_name_lbl.setText(name)
        self._refresh_buttons()
        self._apply_preview()

    def _refresh_buttons(self) -> None:
        tokens = get_tokens(self._fx, self._palette)
        accent = tokens["ACCENT"]
        bg = tokens["BG_SECONDARY"]
        border = tokens["BORDER_COLOR"]
        text = tokens["TEXT_PRIMARY"]
        text_dim = tokens["TEXT_SECONDARY"]

        for name, button in self._preset_btns.items():
            fx, palette = THEME_PRESETS[name]
            preview = get_tokens(fx, palette)
            selected = (self._fx, self._palette) == (fx, palette)
            button.setChecked(selected)
            edge = preview["ACCENT"] if selected else preview["BORDER_COLOR"]
            button.setStyleSheet(
                f"QPushButton {{ background: {preview['BG_SECONDARY']}; color: {preview['TEXT_PRIMARY']};"
                f" border: {2 if selected else 1}px solid {edge}; border-radius: 8px;"
                " font-size: 12px; font-weight: 600; padding: 8px; }"
                f" QPushButton:focus {{ border: 2px solid {preview['BORDER_FOCUS']}; }}"
            )

        for btn in self._fx_btns:
            name = btn.property("fx_name")
            if name == self._fx:
                btn.setStyleSheet(
                    f"background-color: {bg}; border: 2px solid {accent};"
                    f" border-radius: 8px; color: {text}; font-size: 11px;"
                    f" font-weight: 600; text-align: center;"
                )
            else:
                btn.setStyleSheet(
                    f"background-color: {bg}; border: 1px solid {border};"
                    f" border-radius: 8px; color: {text_dim}; font-size: 11px;"
                    f" text-align: center;"
                )

        for btn in self._palette_btns:
            name = btn.property("palette_name")
            pal = PALETTES[name]
            if name == "Dark":
                swatch = "#090B0F"
            elif name == "Light":
                swatch = "#FFFFFF"
            else:
                swatch = pal["ACCENT"]
            ring = (
                f"3px solid {text}"
                if name == self._palette
                else "1px solid #94A3B8"
            )
            btn.setStyleSheet(
                f"background-color: {swatch}; border: {ring}; border-radius: 24px;"
            )

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
        self._preview_navigation_and_weight(main, tokens)
        self._refresh_buttons()

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
