"""
Dynamic stylesheet builder for JobTracker.
Takes a token dict from themes.get_tokens() and returns a full QSS string.
"""


def build_stylesheet(t: dict) -> str:
    """Build the complete application stylesheet from theme tokens."""
    return f"""
/* ── Global ────────────────────────────────────────────────────── */
QMainWindow {{
    background: transparent;
    color: {t['TEXT_PRIMARY']};
}}
QDialog {{
    background-color: {t['BG_PRIMARY']};
    color: {t['TEXT_PRIMARY']};
}}
QWidget {{
    color: {t['TEXT_PRIMARY']};
    font-family: "SF Pro Display", "SF Pro Text", -apple-system,
                 BlinkMacSystemFont, "Helvetica Neue", Arial, sans-serif;
    font-size: 13px;
}}

/* ── Buttons ───────────────────────────────────────────────────── */
QPushButton {{
    background-color: {t['BG_SECONDARY']};
    border: 1px solid {t['BORDER_COLOR']};
    border-radius: 6px;
    padding: 7px 14px;
    color: {t['TEXT_PRIMARY']};
    font-weight: 500;
}}
QPushButton:hover {{
    background-color: {t['BG_TERTIARY']};
    border-color: {t['ACCENT']};
}}
QPushButton:pressed {{
    background-color: {t['BG_PRIMARY']};
}}
QPushButton#primaryBtn {{
    background-color: {t['ACCENT']};
    color: {_contrast_text(t['ACCENT'])};
    font-weight: 600;
    border: none;
    padding: 8px 18px;
    border-radius: 6px;
}}
QPushButton#primaryBtn:hover {{
    background-color: {_darken_hex(t['ACCENT'])};
}}
QPushButton#taskBtn {{
    background-color: {t['BG_SECONDARY']};
    border: 1px solid {t['ACCENT']};
    color: {t['TEXT_PRIMARY']};
    font-weight: 600;
    border-radius: 6px;
    padding: 8px 16px;
}}
QPushButton#taskBtn:hover {{
    background-color: {t['BG_TERTIARY']};
    border-color: {t['ACCENT']};
}}
QPushButton#startBtn {{
    background-color: {t['ACCENT_GREEN']};
    color: white;
    font-weight: 600;
    border: none;
    padding: 8px 16px;
    border-radius: 6px;
}}
QPushButton#startBtn:hover {{
    background-color: {_darken_hex(t['ACCENT_GREEN'])};
}}
QPushButton#startBtn:disabled {{
    background-color: {t['BG_TERTIARY']};
    color: {t['TEXT_DIMMED']};
}}
QPushButton#stopBtn {{
    background-color: {t['ACCENT_RED']};
    color: white;
    font-weight: 600;
    border: none;
    padding: 10px 24px;
    border-radius: 10px;
}}
QPushButton#stopBtn:hover {{
    background-color: {_darken_hex(t['ACCENT_RED'])};
}}
QPushButton#dangerBtn {{
    background-color: {t['ACCENT_RED']};
    color: white;
    font-weight: 600;
    border: none;
}}
QPushButton#dangerBtn:hover {{
    background-color: {_darken_hex(t['ACCENT_RED'])};
}}
QPushButton#gearBtn {{
    background-color: transparent;
    border: none;
    font-size: 18px;
    padding: 4px 8px;
    color: {t['TEXT_SECONDARY']};
}}
QPushButton#gearBtn:hover {{
    color: {t['ACCENT']};
}}
QPushButton#navBtn {{
    border: none;
    border-radius: 12px;
    margin: 4px;
    background-color: transparent;
    color: {t['TEXT_SECONDARY']};
    font-size: 12px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.6px;
}}
QPushButton#navBtn:hover {{
    background-color: {t['BG_TERTIARY']};
    color: {t['TEXT_PRIMARY']};
}}
QPushButton#navBtn:checked {{
    background-color: {t['ACCENT']};
    color: white;
}}
QFrame#bottomNav {{
    border: 1px solid {t['BORDER_COLOR']};
    border-radius: 16px;
    background-color: {t['BG_SECONDARY']};
}}

/* ── Title ─────────────────────────────────────────────────────── */
QLabel#title {{
    font-size: 22px;
    font-weight: 700;
    letter-spacing: -0.5px;
    color: {t['TEXT_PRIMARY']};
}}
QLabel#subtitle {{
    font-size: 12px;
    font-weight: 500;
    color: {t['TEXT_SECONDARY']};
    text-transform: uppercase;
    letter-spacing: 1px;
}}
QLabel#tasksLabel {{
    font-size: 18px;
    font-weight: 700;
    color: {t['TEXT_PRIMARY']};
}}

/* ── Inputs ────────────────────────────────────────────────────── */
QLineEdit, QTextEdit {{
    background-color: {t['BG_PRIMARY']};
    border: 1px solid {t['BORDER_COLOR']};
    border-radius: 6px;
    padding: 8px 10px;
    color: {t['TEXT_PRIMARY']};
    selection-background-color: {t['ACCENT']};
}}
QLineEdit:focus, QTextEdit:focus {{
    border: 1.5px solid {t['BORDER_FOCUS']};
}}
QDateTimeEdit {{
    background-color: {t['BG_PRIMARY']};
    border: 1px solid {t['BORDER_COLOR']};
    border-radius: 6px;
    padding: 8px 10px;
    color: {t['TEXT_PRIMARY']};
}}
QDateTimeEdit:focus {{
    border: 1.5px solid {t['BORDER_FOCUS']};
}}
QCheckBox {{
    spacing: 6px;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {t['BORDER_COLOR']};
    border-radius: 3px;
    background: {t['BG_PRIMARY']};
}}
QCheckBox::indicator:checked {{
    background: {t['ACCENT_GREEN']};
    border: 1px solid {t['ACCENT_GREEN']};
}}

/* ── Combo Box ─────────────────────────────────────────────────── */
QComboBox {{
    background-color: {t['BG_SECONDARY']};
    border: 1px solid {t['BORDER_COLOR']};
    border-radius: 6px;
    padding: 5px 10px;
    color: {t['TEXT_PRIMARY']};
    min-width: 120px;
}}
QComboBox:hover {{
    border-color: {t['ACCENT']};
}}
QComboBox::drop-down {{
    border: none;
    width: 20px;
}}
QComboBox QAbstractItemView {{
    background-color: {t['BG_SECONDARY']};
    border: 1px solid {t['BORDER_COLOR']};
    color: {t['TEXT_PRIMARY']};
    selection-background-color: {t['BG_TERTIARY']};
    outline: none;
}}

/* ── List Widget ───────────────────────────────────────────────── */
QListWidget {{
    background-color: transparent;
    border: none;
    outline: none;
}}
QListWidget::item {{
    background: transparent;
    border: none;
    margin: 0;
    padding: 0;
}}
QListWidget::item:selected {{
    background: transparent;
    border: none;
}}

/* ── Scroll Area ───────────────────────────────────────────────── */
QScrollArea {{
    border: none;
    background: transparent;
}}
QScrollBar:vertical {{
    background-color: transparent;
    width: 8px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background-color: {t['BORDER_COLOR']};
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background-color: {t['TEXT_DIMMED']};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: none;
}}
QScrollBar:horizontal {{
    background-color: transparent;
    height: 8px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background-color: {t['BORDER_COLOR']};
    border-radius: 4px;
    min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{
    background-color: {t['TEXT_DIMMED']};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    background: none;
}}

/* ── Context Menu ──────────────────────────────────────────────── */
QMenu {{
    background-color: {t['BG_SECONDARY']};
    border: 1px solid {t['BORDER_COLOR']};
    border-radius: 6px;
    padding: 4px;
    color: {t['TEXT_PRIMARY']};
}}
QMenu::item {{
    padding: 6px 24px 6px 12px;
    border-radius: 4px;
}}
QMenu::item:selected {{
    background-color: {t['BG_TERTIARY']};
}}
QMenu::separator {{
    height: 1px;
    background: {t['BORDER_COLOR']};
    margin: 4px 8px;
}}

/* ── Message Box ───────────────────────────────────────────────── */
QMessageBox {{
    background-color: {t['BG_PRIMARY']};
}}
QMessageBox QLabel {{
    color: {t['TEXT_PRIMARY']};
}}
"""


def _darken_hex(hex_color: str, amount: int = 25) -> str:
    """Simple hex darkening — clamp each channel down by `amount`."""
    hex_color = hex_color.lstrip("#")[:6]
    try:
        r = max(0, int(hex_color[0:2], 16) - amount)
        g = max(0, int(hex_color[2:4], 16) - amount)
        b = max(0, int(hex_color[4:6], 16) - amount)
        return f"#{r:02x}{g:02x}{b:02x}"
    except (ValueError, IndexError):
        return "#000000"


def _contrast_text(hex_color: str) -> str:
    """Pick dark or light text depending on background luminance."""
    value = hex_color.lstrip("#")[:6]
    try:
        r = int(value[0:2], 16)
        g = int(value[2:4], 16)
        b = int(value[4:6], 16)
    except (ValueError, IndexError):
        return "#FFFFFF"

    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
    return "#0B1120" if luminance > 0.62 else "#FFFFFF"
