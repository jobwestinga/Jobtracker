"""Fast native weight entry and a chart that never makes network requests."""

from __future__ import annotations

import logging
import math

from PySide6.QtCore import QDate, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDateEdit,
    QDoubleSpinBox, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPushButton, QScrollArea, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from ...core import timeutils, weight_plot
from ...core.themes import DEFAULT_TOKENS
from .dialog_utils import question

logger = logging.getLogger("jobtracker")


def _weight_text(value):
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _current_date():
    return QDate.currentDate()


class WeightChart(QWidget):
    entry_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(220)
        self.setMouseTracking(True)
        self._tokens = DEFAULT_TOKENS
        self._points = []
        self._trend = []
        self._positions = []
        self._contrast = False

    def set_tokens(self, tokens, contrast=False):
        self._tokens, self._contrast = tokens, contrast
        self.update()

    def set_data(self, points, trend):
        self._points, self._trend = points, trend
        self._positions = []
        self.setToolTip("")
        self.update()

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        t = self._tokens
        painter.fillRect(self.rect(), QColor(t["BG_SECONDARY"]))
        painter.setPen(QColor(t["TEXT_PRIMARY"] if self._contrast else t["TEXT_SECONDARY"]))
        if not self._points:
            painter.drawText(self.rect(), Qt.AlignCenter, "No measurements in this range")
            return
        rect = QRectF(48, 16, max(1, self.width() - 64), max(1, self.height() - 49))
        xs = [point.x for point in self._points]
        ys = [point.y for point in self._points + self._trend]
        first, last = min(xs), max(xs)
        if first == last:
            first, last = first - 0.5, last + 0.5
        low, high = min(ys), max(ys)
        pad = max(0.3, (high - low) * 0.12)
        low, high = low - pad, high + pad

        def project(point):
            return QPointF(rect.left() + (point.x - first) / (last - first) * rect.width(),
                           rect.bottom() - (point.y - low) / (high - low) * rect.height())

        grid = QColor(t["TEXT_SECONDARY"])
        grid.setAlpha(130 if self._contrast else 45)
        for fraction in (0, 0.5, 1):
            y = rect.bottom() - fraction * rect.height()
            painter.setPen(QPen(grid, 1))
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            painter.setPen(QColor(t["TEXT_PRIMARY"] if self._contrast else t["TEXT_SECONDARY"]))
            painter.drawText(QRectF(0, y - 10, 42, 20), Qt.AlignRight | Qt.AlignVCenter,
                             f"{low + (high - low) * fraction:.1f}")
        painter.drawText(QRectF(rect.left(), rect.bottom() + 8, rect.width() / 2, 20),
                         Qt.AlignLeft, weight_plot.date_label(min(xs), "%d %b %Y"))
        painter.drawText(QRectF(rect.center().x(), rect.bottom() + 8, rect.width() / 2, 20),
                         Qt.AlignRight, weight_plot.date_label(max(xs), "%d %b %Y"))
        painter.save()
        painter.setClipRect(rect.adjusted(-4, -4, 4, 4))
        if self._trend:
            path = QPainterPath()
            path.moveTo(project(self._trend[0]))
            for point in self._trend[1:]:
                path.lineTo(project(point))
            painter.setPen(QPen(QColor(t["ACCENT"]), 3 if self._contrast else 2.2))
            painter.drawPath(path)
        self._positions = []
        painter.setPen(Qt.NoPen)
        for point in self._points:
            position = project(point)
            self._positions.append((position, point))
            color = QColor(t["TEXT_PRIMARY"] if self._contrast else t["ACCENT"])
            color.setAlpha(255 if self._contrast else 145)
            painter.setBrush(color)
            painter.drawEllipse(position, 3, 3)
        painter.restore()

    def _near(self, position):
        if not self._positions:
            return None
        distance, point = min((((position.x() - xy.x()) ** 2 + (position.y() - xy.y()) ** 2, point)
                               for xy, point in self._positions), key=lambda item: item[0])
        return point if distance <= 144 else None

    def mouseMoveEvent(self, event):  # noqa: N802
        point = self._near(event.position())
        self.setCursor(Qt.PointingHandCursor if point else Qt.ArrowCursor)
        self.setToolTip(f"{point.row['measured_at'][:10]}\n{_weight_text(point.y)} kg" if point else "")
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):  # noqa: N802
        point = self._near(event.position())
        if event.button() == Qt.LeftButton and point and point.row.get("uid"):
            self.entry_clicked.emit(point.row["uid"])
        super().mousePressEvent(event)


class WeightView(QWidget):
    changed = Signal()

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self._tokens = DEFAULT_TOKENS
        self._rows = []
        self._loaded = False
        self._editing_uid = None
        self._editing_timestamp = None
        self._editing_source = None
        self._chart_key = None
        self._exact_value = None
        self._busy = False
        self._calendar_day = _current_date()
        self._follow_today = True
        self._draft_dirty = False
        self._whole = self._read_int("weight_picker_whole", 0, 1000)
        self._decimal = self._read_int("weight_picker_decimal", 0, 9)
        self._center = self._whole if self._whole is not None else 80
        self._build_ui()
        self.refresh(initial=True)
        self.set_tokens(DEFAULT_TOKENS)
        # One cheap date comparison per minute; chart fitting and DB reads only
        # run when the calendar actually changes. Child timer stops on disposal.
        self._day_timer = QTimer(self)
        self._day_timer.setInterval(60_000)
        self._day_timer.timeout.connect(self._check_day)
        self._day_timer.start()

    def _read_int(self, key, low, high):
        try:
            value = int(self.service.get_setting(key, ""))
            return value if low <= value <= high else None
        except (TypeError, ValueError):
            return None

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = self._scroll = QScrollArea()
        scroll.setObjectName("weightScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._body = QWidget()
        self._body.setObjectName("weightBody")
        scroll.setWidget(self._body)
        outer.addWidget(scroll)
        self._layout = QVBoxLayout(self._body)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(12)
        header = QHBoxLayout()
        title = QLabel("Weight"); title.setObjectName("weightTitle")
        header.addWidget(title)
        header.addStretch()
        options_button = QPushButton("Options")
        options_button.setCheckable(True)
        options_button.clicked.connect(lambda checked: self._options.setVisible(checked))
        header.addWidget(options_button)
        self._layout.addLayout(header)

        self._entry = QFrame(); self._entry.setObjectName("weightCard")
        entry = QVBoxLayout(self._entry)
        entry.setContentsMargins(12, 12, 12, 12)
        entry.setSpacing(8)
        date_row = QHBoxLayout()
        date_row.addWidget(QLabel("Date"))
        self._date = QDateEdit(_current_date())
        self._date.setCalendarPopup(True)
        self._date.setDisplayFormat("ddd d MMM yyyy")
        self._date.setDateRange(QDate(1, 1, 1), QDate(9999, 12, 31))
        self._date.dateChanged.connect(self._date_changed)
        date_row.addWidget(self._date, 1)
        today = QPushButton("Today")
        today.clicked.connect(self._today)
        date_row.addWidget(today)
        entry.addLayout(date_row)
        preview_row = QHBoxLayout()
        self._preview = QLabel("Choose kg and decimal"); self._preview.setObjectName("weightPreview")
        preview_row.addWidget(self._preview, 1)
        self._save_button = QPushButton("Save weight")
        self._save_button.setObjectName("primaryBtn")
        self._save_button.clicked.connect(self._save)
        entry.addLayout(preview_row)
        whole_row = QHBoxLayout(); whole_row.setSpacing(4)
        lower = QPushButton("‹")
        lower.setObjectName("weightShift")
        lower.setToolTip("Lower kilogram range")
        lower.clicked.connect(lambda: self._shift_range(-7))
        whole_row.addWidget(lower)
        self._whole_group = QButtonGroup(self)
        self._whole_buttons = []
        for index in range(7):
            button = QPushButton(); button.setCheckable(True); button.setObjectName("weightTile")
            button.clicked.connect(lambda _checked=False, tile=button: self._select_whole(int(tile.text())))
            self._whole_group.addButton(button)
            self._whole_buttons.append(button)
            whole_row.addWidget(button, 1)
        higher = QPushButton("›"); higher.setObjectName("weightShift")
        higher.setToolTip("Higher kilogram range")
        higher.clicked.connect(lambda: self._shift_range(7))
        whole_row.addWidget(higher)
        entry.addLayout(whole_row)
        decimal_row = QHBoxLayout(); decimal_row.setSpacing(4)
        self._decimal_group = QButtonGroup(self)
        self._decimal_buttons = []
        for decimal in range(10):
            button = QPushButton(f".{decimal}"); button.setCheckable(True); button.setObjectName("weightTile")
            button.clicked.connect(lambda _checked=False, value=decimal: self._select_decimal(value))
            self._decimal_group.addButton(button)
            self._decimal_buttons.append(button)
            decimal_row.addWidget(button, 1)
        entry.addLayout(decimal_row)
        entry.addWidget(self._save_button)
        self._status = QLabel("Select both rows, then save. Choosing a date opens that day's entry.")
        self._status.setWordWrap(True); self._status.setObjectName("weightHint")
        self._status.setTextFormat(Qt.PlainText)
        entry.addWidget(self._status)
        self._layout.addWidget(self._entry)

        self._options = QFrame(); self._options.setObjectName("weightCard")
        options = QVBoxLayout(self._options)
        appearance = QHBoxLayout()
        appearance.addWidget(QLabel("Density"))
        self._density = QComboBox()
        self._density.addItem("Comfortable", "comfortable"); self._density.addItem("Compact", "compact")
        saved_density = self.service.get_setting("weight_ui_density", "comfortable")
        self._density.setCurrentIndex(max(0, self._density.findData(saved_density)))
        self._density.currentIndexChanged.connect(self._appearance_changed)
        appearance.addWidget(self._density)
        self._contrast = QCheckBox("High contrast")
        self._contrast.setChecked(self.service.get_setting("weight_ui_contrast", "0") == "1")
        self._contrast.toggled.connect(self._appearance_changed)
        appearance.addWidget(self._contrast)
        appearance.addStretch()
        options.addLayout(appearance)
        exact_row = QHBoxLayout()
        exact_row.addWidget(QLabel("Precise input (kg)"))
        self._exact = QDoubleSpinBox()
        self._exact.setDecimals(3); self._exact.setRange(0, 1000); self._exact.setSingleStep(0.1)
        self._exact.valueChanged.connect(self._precise_changed)
        exact_row.addWidget(self._exact, 1)
        options.addLayout(exact_row)
        self._options.hide()
        self._layout.addWidget(self._options)

        chart_card = QFrame(); chart_card.setObjectName("weightCard")
        chart_layout = QVBoxLayout(chart_card)
        chart_controls = QHBoxLayout()
        self._range = QComboBox()
        for label, value in (("30 days", "30"), ("90 days", "90"), ("1 year", "365"), ("All history", "all")):
            self._range.addItem(label, value)
        self._range.setCurrentIndex(max(0, self._range.findData(self.service.get_setting("weight_chart_range", "90"))))
        self._range.currentIndexChanged.connect(self._chart_options_changed)
        chart_controls.addWidget(self._range, 1)
        self._smoothing = QComboBox()
        for label, value in (("Original curve", "loess"), ("7-day average", "mean7"), ("Measurements", "none")):
            self._smoothing.addItem(label, value)
        self._smoothing.setCurrentIndex(max(0, self._smoothing.findData(self.service.get_setting("weight_chart_smoothing", "loess"))))
        self._smoothing.currentIndexChanged.connect(self._chart_options_changed)
        chart_controls.addWidget(self._smoothing, 1)
        chart_layout.addLayout(chart_controls)
        detail = QHBoxLayout()
        self._historic = QCheckBox("Historic readings")
        self._historic.setChecked(self.service.get_setting("weight_chart_historic", "0") == "1")
        self._historic.toggled.connect(self._chart_options_changed)
        detail.addWidget(self._historic)
        detail.addStretch()
        detail.addWidget(QLabel("Curve span"))
        self._span = QDoubleSpinBox(); self._span.setRange(0.01, 1); self._span.setDecimals(2); self._span.setSingleStep(0.01)
        try:
            span = float(self.service.get_setting("weight_chart_span", "0.04"))
        except (TypeError, ValueError):
            span = 0.04
        self._span.setValue(span if math.isfinite(span) else 0.04)
        self._span.valueChanged.connect(self._chart_options_changed)
        detail.addWidget(self._span)
        chart_layout.addLayout(detail)
        self._chart = WeightChart()
        self._chart.entry_clicked.connect(self.edit_entry)
        chart_layout.addWidget(self._chart, 1)
        self._summary = QLabel(); self._summary.setObjectName("weightHint")
        self._summary.setWordWrap(True)
        chart_layout.addWidget(self._summary)
        self._layout.addWidget(chart_card)

        history_card = QFrame(); history_card.setObjectName("weightCard")
        history_layout = QVBoxLayout(history_card)
        history_header = QHBoxLayout()
        history_header.addWidget(QLabel("History"))
        self._search = QLineEdit(); self._search.setPlaceholderText("Search date or source")
        self._search.textChanged.connect(self._filter_history)
        history_header.addWidget(self._search, 1)
        history_layout.addLayout(history_header)
        self._history = QTreeWidget()
        self._history.setHeaderLabels(["Date", "kg", "Source"])
        self._history.setRootIsDecorated(False)
        self._history.setAlternatingRowColors(False)
        self._history.setSelectionMode(QAbstractItemView.SingleSelection)
        self._history.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self._history.setMinimumHeight(160)
        self._history.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self._history.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._history.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._history.itemDoubleClicked.connect(lambda item, _column: self.edit_entry(item.data(0, Qt.UserRole)))
        self._history.itemSelectionChanged.connect(self._history_selection_changed)
        history_layout.addWidget(self._history, 1)
        history_actions = QHBoxLayout()
        self._edit_button = QPushButton("Edit selected")
        self._edit_button.clicked.connect(lambda: self.edit_entry(self._selected_uid()))
        self._delete_button = QPushButton("Delete selected")
        self._delete_button.clicked.connect(self._request_delete)
        history_actions.addWidget(self._edit_button)
        history_actions.addWidget(self._delete_button)
        history_actions.addStretch()
        history_layout.addLayout(history_actions)
        self._layout.addWidget(history_card, 1)
        self._history_selection_changed()

    def set_tokens(self, tokens):
        self._tokens = tokens
        contrast = self._contrast.isChecked()
        compact = self._density.currentData() == "compact"
        t = tokens
        self.setStyleSheet(f"""
            QWidget#weightBody, QScrollArea#weightScroll {{ background: {t['BG_PRIMARY']}; }}
            QFrame#weightCard {{ background: {t['BG_SECONDARY']}; border: 1px solid {t['TEXT_PRIMARY'] if contrast else t['BORDER_COLOR']}; border-radius: 8px; }}
            QLabel#weightTitle {{ font-size: 24px; font-weight: 700; }}
            QLabel#weightPreview {{ font-size: 22px; font-weight: 600; }}
            QLabel#weightHint {{ color: {t['TEXT_PRIMARY'] if contrast else t['TEXT_SECONDARY']}; }}
            QPushButton#weightTile {{ padding: 3px 0; font-size: {'14' if compact else '16'}px; min-width: 26px; border: 1px solid {t['TEXT_PRIMARY'] if contrast else t['BORDER_COLOR']}; }}
            QPushButton#weightTile:checked {{ background: {t['ACCENT']}; color: {'#111111' if QColor(t['ACCENT']).lightness() > 155 else '#ffffff'}; border: 2px solid {t['TEXT_PRIMARY']}; font-weight: 700; }}
            QPushButton#weightShift {{ padding: 3px 0; min-width: 18px; max-width: 22px; }}
            QTreeWidget {{ background: {t['BG_SECONDARY']}; border: none; }}
            QTreeWidget::item {{ min-height: {'24' if compact else '32'}px; }}
            QHeaderView::section {{ background: {t['BG_TERTIARY']}; color: {t['TEXT_PRIMARY'] if contrast else t['TEXT_SECONDARY']}; border: none; padding: 4px; }}
            QDoubleSpinBox {{ background: {t['BG_PRIMARY']}; color: {t['TEXT_PRIMARY']}; border: 1px solid {t['BORDER_COLOR']}; border-radius: 4px; padding: 4px; }}
            QDoubleSpinBox:disabled {{ color: {t['TEXT_DIMMED']}; }}
        """)
        for button in self._whole_buttons + self._decimal_buttons:
            button.setMinimumHeight(33 if compact else 43)
        self._layout.setSpacing(8 if compact else 12)
        self._chart.set_tokens(tokens, contrast)

    def _appearance_changed(self, *_args):
        self.service.set_setting("weight_ui_density", self._density.currentData())
        self.service.set_setting("weight_ui_contrast", "1" if self._contrast.isChecked() else "0")
        self.set_tokens(self._tokens)

    def _chart_options_changed(self, *_args):
        for key, value in (("weight_chart_range", self._range.currentData()),
                           ("weight_chart_smoothing", self._smoothing.currentData()),
                           ("weight_chart_historic", "1" if self._historic.isChecked() else "0"),
                           ("weight_chart_span", str(self._span.value()))):
            self.service.set_setting(key, value)
        self._update_chart()

    def _shift_range(self, delta):
        self._center = min(997, max(3, self._center + delta))
        self._clear_outside_selection()
        self._render_picker()

    def _clear_outside_selection(self):
        first = min(994, max(0, self._center - 3))
        if self._whole is not None and not first <= self._whole <= first + 6:
            self._whole = self._exact_value = None
            self.service.set_setting("weight_picker_whole", "")

    def _center_latest(self):
        if self._rows:
            self._center = math.floor(self._rows[-1]["weight_kg"] + 0.5)
            self._clear_outside_selection()

    def _select_whole(self, value):
        self._whole, self._exact_value = value, None
        self._draft_dirty = True
        self.service.set_setting("weight_picker_whole", str(value))
        self._render_picker()

    def _select_decimal(self, value):
        self._decimal, self._exact_value = value, None
        self._draft_dirty = True
        self.service.set_setting("weight_picker_decimal", str(value))
        self._render_picker()

    def _precise_changed(self, value):
        self._exact_value = value
        self._draft_dirty = True
        tenths = math.floor(value * 10 + 0.5)
        self._whole, self._decimal = divmod(tenths, 10)
        self._center = math.floor(value + 0.5)
        self.service.set_setting("weight_picker_whole", str(self._whole))
        self.service.set_setting("weight_picker_decimal", str(self._decimal))
        self._render_picker()

    def _value(self):
        if self._exact_value is not None:
            return self._exact_value
        return self._whole + self._decimal / 10 if self._whole is not None and self._decimal is not None else None

    def _render_picker(self):
        first = min(994, max(0, self._center - 3))
        for group in (self._whole_group, self._decimal_group):
            group.setExclusive(False)
        for index, button in enumerate(self._whole_buttons):
            value = first + index
            button.setText(str(value))
            button.setChecked(value == self._whole)
            button.setAccessibleName(f"{value} kilograms")
        for index, button in enumerate(self._decimal_buttons):
            button.setChecked(index == self._decimal)
            button.setAccessibleName(f"Decimal {index}")
        for group in (self._whole_group, self._decimal_group):
            group.setExclusive(True)
        value = self._value()
        self._preview.setText(f"{_weight_text(value)} kg" if value is not None else "Choose kg and decimal")
        self._save_button.setEnabled(not self._busy and value is not None and 0 < value <= 1000)
        self._save_button.setText("Update weight" if self._editing_uid else "Save weight")
        if value is not None:
            self._exact.blockSignals(True)
            self._exact.setValue(value)
            self._exact.blockSignals(False)

    def _date_changed(self, _date):
        self._follow_today = self._date.date() == _current_date()
        self._draft_dirty = False
        self._load_daily_entry()
        self._status.setText("Choosing a date opens that day's entry. Select both rows, then save.")

    def _today(self):
        if self._date.date() == _current_date():
            self._date_changed(self._date.date())
        else:
            self._date.setDate(_current_date())
        self._center_latest()
        self._render_picker()

    def _load_daily_entry(self):
        day = self._date.date().toString(Qt.ISODate)
        row = next((row for row in reversed(self._rows) if row.get("source") == "manual" and row["measured_at"][:10] == day), None)
        self._editing_uid = self._editing_timestamp = None
        self._editing_source = None
        if row:
            self._load_entry(row)
        else:
            self._render_picker()

    def _load_entry(self, row):
        self._editing_uid, self._editing_timestamp = row["uid"], row["measured_at"]
        self._editing_source = row.get("source", "manual")
        self._exact_value = row["weight_kg"]
        tenths = math.floor(self._exact_value * 10 + 0.5)
        self._whole, self._decimal = divmod(tenths, 10)
        self._center = math.floor(self._exact_value + 0.5)
        self._render_picker()

    def edit_entry(self, uid):
        row = next((row for row in self._rows if row.get("uid") == uid), None)
        if row is None:
            return
        self._follow_today = False
        self._draft_dirty = False
        self._date.blockSignals(True)
        self._date.setDate(QDate.fromString(row["measured_at"][:10], Qt.ISODate))
        self._date.blockSignals(False)
        self._load_entry(row)
        self._status.setText("Editing this reading. Imported precision is kept until you change a tile.")
        self._scroll.ensureWidgetVisible(self._entry)

    def _check_day(self):
        if _current_date() != self._calendar_day:
            self.refresh()

    def refresh(self, *, initial=False):
        try:
            rows = [point.row for point in weight_plot.points_for(self.service.list_weight_measurements(), historic=True)]
        except Exception:
            logger.exception("Could not load weight history")
            self._status.setText("Could not load weight history. Your selection is kept; try again.")
            return
        changed = rows != self._rows or not self._loaded
        self._rows, self._loaded = rows, True
        current_day = _current_date()
        if self._follow_today and not self._draft_dirty and self._date.date() != current_day:
            self._date.setDate(current_day)
            self._center_latest()
        self._calendar_day = current_day
        if self._editing_uid and self._editing_source == "manual" and not any(row["uid"] == self._editing_uid for row in rows):
            # Daily saves from two devices can merge into a canonical uid.
            # Preserve the draft while following the same calendar day's row.
            day = self._date.date().toString(Qt.ISODate)
            canonical = next((row for row in reversed(rows) if row.get("source") == "manual" and row["measured_at"][:10] == day), None)
            if canonical:
                self._editing_uid, self._editing_timestamp = canonical["uid"], canonical["measured_at"]
        if initial:
            self._load_daily_entry()
            self._center_latest()
        if changed:
            selected = self._selected_uid()
            self._history.clear()
            for row in reversed(rows):
                item = QTreeWidgetItem([row["measured_at"][:10], _weight_text(row["weight_kg"]), row.get("source", "manual")])
                item.setData(0, Qt.UserRole, row["uid"])
                self._history.addTopLevelItem(item)
                if row["uid"] == selected:
                    self._history.setCurrentItem(item)
            self._filter_history()
        self._update_chart()
        self._render_picker()

    def _update_chart(self):
        end = _current_date().toPython()
        choice = self._range.currentData()
        mode = self._smoothing.currentData()
        key = (tuple((row["uid"], row["measured_at"], row["weight_kg"], row.get("source")) for row in self._rows), end,
               choice, mode, self._span.value(), self._historic.isChecked())
        if key == self._chart_key:
            return
        self._chart_key = key
        start = weight_plot.range_start(end, int(choice)) if choice != "all" else None
        points = weight_plot.points_for(self._rows, start=start, end=end if start else None, historic=self._historic.isChecked())
        self._span.setEnabled(mode == "loess")
        self._chart.set_data(points, weight_plot.trend_for(points, mode, self._span.value()))
        self._summary.setText(f"{len(points)} readings · latest {_weight_text(points[-1].y)} kg · change {points[-1].y - points[0].y:+.2f} kg" if points else "Save a reading to start your chart. Click a dot or a history row to edit.")

    def _save(self):
        self._check_day()
        value = self._value()
        if self._busy or value is None or not 0 < value <= 1000:
            return
        self._busy = True
        self._render_picker()
        day = self._date.date().toString(Qt.ISODate)
        timestamp = self._editing_timestamp if self._editing_uid and self._editing_timestamp[:10] == day else day
        try:
            result = self.service.upsert_weight(timestamp, value, weight_uid=self._editing_uid)
            if result.get("skipped"):
                self._status.setText("This entry was removed. Choose its date again to add a new reading.")
            else:
                self._load_entry(result["measurement"])
                self._draft_dirty = False
                self._status.setText("Saved.")
                self.changed.emit()
            self.refresh()
            if not result.get("skipped"):
                self._center_latest()
        except Exception as error:
            logger.exception("Could not save weight")
            self._status.setText(f"Could not save: {error}. Your selection is kept.")
        finally:
            self._busy = False
            self._render_picker()

    def _selected_uid(self):
        selected = self._history.selectedItems()
        return selected[0].data(0, Qt.UserRole) if selected else None

    def _history_selection_changed(self):
        enabled = bool(self._selected_uid())
        self._edit_button.setEnabled(enabled)
        self._delete_button.setEnabled(enabled)

    def _filter_history(self, *_args):
        query = self._search.text().strip().casefold()
        for index in range(self._history.topLevelItemCount()):
            item = self._history.topLevelItem(index)
            item.setHidden(bool(query) and query not in " ".join(item.text(column) for column in range(3)).casefold())

    def _request_delete(self):
        uid = self._selected_uid()
        if not uid:
            return
        question(self, "Delete weight reading?", "Delete the selected reading from your history?",
                 lambda answer: self._delete_entry(uid) if answer == QMessageBox.Yes else None)

    def _delete_entry(self, uid):
        try:
            deleted = self.service.delete_weight(uid)
            if uid == self._editing_uid:
                self._editing_uid = self._editing_timestamp = None
                self._editing_source = None
            self.refresh()
            self._status.setText("Deleted." if deleted else "This reading was already removed.")
            self.changed.emit()
        except Exception as error:
            logger.exception("Could not delete weight")
            self._status.setText(f"Could not delete: {error}")
