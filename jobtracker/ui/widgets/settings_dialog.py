"""
Settings dialog — theme FX, colour palette, and data management.
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QPushButton, QFrame, QMessageBox, QFileDialog, QComboBox,
)
from PySide6.QtCore import Qt, QTimer
import json
import logging
import zipfile
from typing import Optional

from ...services.tracker_service import TrackerService
from ...core.themes import PALETTES, PALETTE_NAMES, FX_NAMES, get_tokens
from ...core import export_bundle
from ...core.timeutils import parse_day_start

logger = logging.getLogger("jobtracker")


class SettingsDialog(QDialog):
    def __init__(self, parent=None, service=None) -> None:
        super().__init__(parent)
        # All persistence goes through the service (not the db directly).
        self._svc = service or getattr(parent, "service", None) or TrackerService()
        self.setWindowTitle("Settings")
        self.setFixedSize(440, 600)

        # Load current prefs
        self._fx = self._svc.get_setting("theme_fx", "Glow")
        self._palette = self._svc.get_setting("theme_palette", "Ocean")
        self._day_start = self._svc.get_setting("day_start_time", "03:00")
        self._original_fx = self._fx
        self._original_palette = self._palette

        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(18)

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
            pal = PALETTES[name]
            btn = QPushButton()
            btn.setFixedSize(48, 48)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(name)
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
        self.day_start_combo.setCurrentIndex(parse_day_start(self._day_start).hour)
        self.day_start_combo.setMinimumHeight(32)
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

    # ── Selection ────────────────────────────────────────────────────────
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
        if main and hasattr(main, "_heatmap_view"):
            main._heatmap_view.set_tokens(tokens)
        self._refresh_buttons()

    def _cancel(self) -> None:
        """Revert preview to original theme."""
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
            if main and hasattr(main, "_heatmap_view"):
                main._heatmap_view.set_tokens(tokens)
        self.reject()

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
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Backup Bundle", "jobtracker_backup.zip", "Zip bundle (*.zip)"
        )
        if not path:
            return
        try:
            export_data = self._svc.export_data()
            # Daily summary CSV respects the logical day-start setting.
            breakdown = self._svc.get_subject_breakdown(grouping="daily", days=None)
            export_bundle.write_zip(path, export_data, breakdown)
            QMessageBox.information(
                self, "Exported",
                "Backup bundle saved:\n• jobtracker_backup.json (restore file)\n"
                "• sessions.csv, subjects.csv, daily_summary.csv",
            )
        except Exception as exc:
            logger.exception("Export failed")
            QMessageBox.critical(self, "Error", f"Export failed:\n{exc}")

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Backup Bundle",
            "",
            "JobTracker backup bundle (*.zip *.json)",
        )
        if not path:
            return
        if QMessageBox.question(
            self, "Import Data",
            "This will merge the backup into your existing data.\nProceed?",
            QMessageBox.Yes | QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        try:
            if path.lower().endswith(".zip"):
                with zipfile.ZipFile(path) as zf:
                    data = json.loads(zf.read(export_bundle.JSON_FILENAME))
            else:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
            self._svc.import_data(data)
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
            QMessageBox.information(self, "Imported", "Data restored successfully.")
            self.reject()
        except Exception as exc:
            logger.exception("Import failed")
            QMessageBox.critical(self, "Error", f"Import failed:\n{exc}")

    # ── Result ───────────────────────────────────────────────────────────
    def get_settings(self) -> dict:
        hour = self.day_start_combo.currentData()
        return {
            "theme_fx": self._fx,
            "theme_palette": self._palette,
            "day_start_time": f"{int(hour):02d}:00",
        }
