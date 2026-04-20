"""
Settings dialog — theme FX, colour palette, and data management.
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QPushButton, QFrame, QMessageBox, QFileDialog,
)
from PySide6.QtCore import Qt, QTimer
import json
from typing import Optional

from ...core.database import db
from ...core.themes import PALETTES, PALETTE_NAMES, FX_NAMES, get_tokens


class SettingsDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setFixedSize(440, 500)

        # Load current prefs
        self._fx = db.get_setting("theme_fx", "Glow")
        self._palette = db.get_setting("theme_palette", "Ocean")
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
        max_cols = 5
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
            ring = f"3px solid {text}" if name == self._palette else "3px solid transparent"
            btn.setStyleSheet(
                f"background-color: {pal['ACCENT']}; border: {ring}; border-radius: 24px;"
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
        path, _ = QFileDialog.getSaveFileName(self, "Export Backup", "", "JSON (*.json)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(db.export_data(), f, indent=2)
            QMessageBox.information(self, "Exported", "Backup saved successfully.")

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import Backup", "", "JSON (*.json)")
        if not path:
            return
        if QMessageBox.question(
            self, "Import Data",
            "This will merge the backup into your existing data.\nProceed?",
            QMessageBox.Yes | QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            db.import_data(data)
            QMessageBox.information(self, "Imported", "Data restored successfully.")
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Import failed:\n{exc}")

    # ── Result ───────────────────────────────────────────────────────────
    def get_settings(self) -> dict:
        return {"theme_fx": self._fx, "theme_palette": self._palette}
