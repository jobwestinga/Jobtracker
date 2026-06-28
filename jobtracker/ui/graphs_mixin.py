"""
Graphs page: bar chart / agenda / heatmap building, reload, and settings.

Mixed into MainWindow. Relies on shared attributes/methods of the host window:
``self.service``, ``self._pages``, ``self._open_settings`` and the persisted graph
state (``self._graph_*``).
"""

from __future__ import annotations

import logging
import math
from datetime import date, datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from ..core.timeutils import (
    agenda_hour,
    agenda_hour_label,
    graph_preset_window,
    grouping_for_preset,
    grouping_for_span,
    logical_day,
    parse_iso,
)
from .widgets.agenda_view import AgendaViewWidget
from .widgets.day_sessions_dialog import DaySessionsDialog
from .widgets.graph_settings_dialog import GraphSettingsDialog
from .widgets.graphs_view import WorkGraphWidget
from .widgets.heatmap_view import HeatmapWidget

logger = logging.getLogger("jobtracker")


class GraphsMixin:
    # ── persisted-setting loaders (used by MainWindow.__init__) ─────────
    def _load_graph_range_preset(self) -> str:
        raw = self.service.get_setting("graph_range", "weeks")
        legacy_ranges = {"7": "weeks", "14": "weeks", "30": "months"}
        resolved = legacy_ranges.get(raw, raw)
        return resolved if resolved in {"weeks", "months", "year", "all"} else "weeks"

    def _load_custom_range(self) -> tuple[date, date] | None:
        start_raw = self.service.get_setting("graph_custom_start", "")
        end_raw = self.service.get_setting("graph_custom_end", "")
        if (
            self.service.get_setting("graph_range", "weeks") != "custom"
            or not start_raw
            or not end_raw
        ):
            return None
        try:
            return date.fromisoformat(start_raw), date.fromisoformat(end_raw)
        except ValueError:
            return None

    # ── page ────────────────────────────────────────────────────────────
    def _build_graphs_page(self) -> None:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("Graphs")
        title.setObjectName("title")
        header.addWidget(title)
        header.addStretch()

        gear_btn = QPushButton("⚙")
        gear_btn.setObjectName("gearBtn")
        gear_btn.setMinimumHeight(34)
        gear_btn.setCursor(Qt.PointingHandCursor)
        gear_btn.setToolTip("Settings")
        gear_btn.clicked.connect(self._open_settings)
        header.addWidget(gear_btn)

        lay.addLayout(header)

        view_controls = QHBoxLayout()
        view_controls.setSpacing(7)

        self._graph_mode_buttons: dict[str, QPushButton] = {}
        for shortcut_number, (label, mode) in enumerate(
            (
                ("Stacked Bar", "bar"),
                ("Agenda", "agenda"),
                ("Heatmap", "heatmap"),
            ),
            start=1,
        ):
            button = QPushButton(f"{shortcut_number}  {label}")
            button.setMinimumHeight(34)
            button.setCursor(Qt.PointingHandCursor)
            button.setToolTip(
                f"Press {shortcut_number} to switch to {label}"
            )
            button.clicked.connect(
                lambda _checked=False, selected=mode: self._set_graph_view_mode(selected)
            )
            view_controls.addWidget(button, 1)
            self._graph_mode_buttons[mode] = button
        self._graph_settings_btn = QPushButton("Graph Settings")
        self._graph_settings_btn.setMinimumHeight(34)
        self._graph_settings_btn.setCursor(Qt.PointingHandCursor)
        self._graph_settings_btn.clicked.connect(self._open_graph_settings)
        view_controls.addWidget(self._graph_settings_btn)
        lay.addLayout(view_controls)
        self._sync_graph_mode_buttons()

        self._graph_subtitle = QLabel("")
        self._graph_subtitle.setStyleSheet("font-size: 12px; font-weight: 500;")
        lay.addWidget(self._graph_subtitle)

        self._graph_stack = QStackedWidget()

        self._graph_view = WorkGraphWidget()
        self._graph_stack.addWidget(self._graph_view)  # index 0

        self._agenda_view = AgendaViewWidget()
        self._graph_stack.addWidget(self._agenda_view)  # index 1

        self._heatmap_view = HeatmapWidget()
        self._heatmap_view.day_clicked.connect(self._open_heatmap_day)
        self._graph_stack.addWidget(self._heatmap_view)  # index 2

        lay.addWidget(self._graph_stack, 1)

        self._graph_legend = QLabel("")
        self._graph_legend.setWordWrap(True)
        self._graph_legend.setStyleSheet("font-size: 11px;")
        lay.addWidget(self._graph_legend)

        self._pages.addWidget(page)

    # ── reload + mode ───────────────────────────────────────────────────
    def _reload_graphs(self) -> None:
        if self._graph_view_mode == "agenda":
            self._reload_agenda()
        elif self._graph_view_mode == "heatmap":
            self._reload_heatmap()
        else:
            self._reload_bar_chart()

    def _set_graph_view_mode(self, mode: str) -> None:
        if mode not in {"bar", "agenda", "heatmap"}:
            return
        self._graph_view_mode = mode
        self.service.set_setting("graph_view_mode", mode)
        self._sync_graph_mode_buttons()
        self._reload_graphs()

    def _sync_graph_mode_buttons(self) -> None:
        for mode, button in getattr(self, "_graph_mode_buttons", {}).items():
            button.setObjectName("primaryBtn" if mode == self._graph_view_mode else "")
            button.style().unpolish(button)
            button.style().polish(button)

    def _window_and_grouping(self) -> tuple[date | None, date | None, str]:
        """(start_date, end_date, grouping). Dates are None for all-history; the
        grouping (daily/weekly/monthly) is derived from the chosen range."""
        if self._graph_custom_range:
            start_day, end_day = self._graph_custom_range
            return start_day, end_day, grouping_for_span(start_day, end_day)
        today = logical_day(datetime.now(), self.service.get_day_start())
        window = graph_preset_window(self._graph_range_preset, today)
        grouping = grouping_for_preset(self._graph_range_preset)
        if window is not None:
            return window[0], window[1], grouping
        return None, None, grouping

    def _range_label(self) -> str:
        if self._graph_custom_range:
            start_day, end_day = self._graph_custom_range
            return f"{start_day.isoformat()} → {end_day.isoformat()}"
        return {
            "weeks": "Weeks",
            "months": "Months",
            "year": "Year",
            "all": "All Time",
        }.get(self._graph_range_preset, "Weeks")

    def _set_legend(self, seen: dict[str, str]) -> None:
        self._graph_legend.show()
        if seen:
            parts = [
                f"<span style='color:{color};'>■</span> {name}"
                for name, color in seen.items()
            ]
            self._graph_legend.setText("&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;".join(parts))
        else:
            self._graph_legend.setText("No tracked sessions in the displayed range.")

    def _reload_bar_chart(self) -> None:
        self._graph_stack.setCurrentIndex(0)
        start_day, end_day, grouping = self._window_and_grouping()
        data = self.service.get_subject_breakdown(
            grouping=grouping, days=None,
            start_date=start_day, end_date=end_day,
        )
        fit_width = self.service.get_setting("graph_fit_horizontal", "1") == "1"
        self._graph_view.set_data(data, fit_width, grouping)

        total_seconds = sum(day["total_seconds"] for day in data)
        group_label = {"daily": "Daily", "weekly": "Weekly", "monthly": "Monthly"}.get(
            grouping, "Daily"
        )
        self._graph_subtitle.setText(
            f"{group_label} · {self._range_label()} · {total_seconds / 3600:.1f}h"
        )

        seen: dict[str, str] = {}
        for day in data:
            for seg in day["segments"]:
                seen[seg["subject_name"]] = seg["color"]
        self._set_legend(seen)

    def _reload_agenda(self) -> None:
        self._graph_stack.setCurrentIndex(1)

        day_start = self.service.get_day_start()
        today = logical_day(datetime.now(), day_start)

        start_day, end_day, _grouping = self._window_and_grouping()
        if start_day is None or end_day is None:
            earliest_iso = self.service.get_earliest_session_date()
            parsed = parse_iso(earliest_iso) if earliest_iso else None
            start_day = logical_day(parsed, day_start) if parsed else today
            end_day = today

        day_keys, sessions = self.service.get_agenda_data(start_day, end_day, day_start)

        # Auto-fit the visible hour band to actual work (in agenda-hour space, so
        # late-night work can push the bottom edge past 24 -> "(+1)").
        hour_start = self._graph_hour_start
        hour_end = self._graph_hour_end
        if self.service.get_setting("graph_autofit_hours", "0") == "1" and sessions:
            try:
                min_h = math.floor(min(s["start_h"] for s in sessions))
                max_h = math.ceil(max(s["end_h"] for s in sessions))
                if min_h < max_h:
                    hour_start = max(0, min_h)
                    hour_end = min(27, max(max_h, hour_start + 1))
            except Exception:
                logger.exception("Agenda auto-fit failed; using configured hours")

        fit_width = self.service.get_setting("graph_fit_horizontal", "1") == "1"
        now = datetime.now()
        now_marker = (today.isoformat(), agenda_hour(now, day_start))
        self._agenda_view.set_data(
            sessions, day_keys, hour_start, hour_end, fit_width, now_marker=now_marker
        )

        total_seconds = sum(s.get("duration_seconds", 0) for s in sessions)
        self._graph_subtitle.setText(
            f"Agenda · {self._range_label()} · {total_seconds / 3600:.1f}h · "
            f"{agenda_hour_label(hour_start)}–{agenda_hour_label(hour_end)}"
        )

        seen: dict[str, str] = {}
        for s in sessions:
            seen[s["subject_name"]] = s["color"]
        self._set_legend(seen)

    def _reload_heatmap(self) -> None:
        self._graph_stack.setCurrentIndex(2)
        # Heatmap is intentionally always all-time. Its horizontal scroll keeps
        # the newest weeks in view while preserving immediate access to history.
        rows = self.service.get_heatmap_data(days=None)
        self._heatmap_view.set_data(rows)
        total_seconds = sum(row["total_seconds"] for row in rows)
        self._graph_subtitle.setText(
            f"Heatmap · All Time · {total_seconds / 3600:.1f}h"
        )
        self._graph_legend.hide()

    def _open_heatmap_day(self, day_iso: str) -> None:
        try:
            day = date.fromisoformat(day_iso)
        except ValueError:
            logger.warning("Ignored invalid heatmap day: %r", day_iso)
            return
        DaySessionsDialog(self.service, day, self).exec()
        self._reload_subjects()
        self._reload_graphs()

    def _refresh_graphs_if_needed(self) -> None:
        if self.service.active_session and self._pages.currentIndex() == 2:
            self._reload_graphs()

    def _open_graph_settings(self) -> None:
        dlg = GraphSettingsDialog(self, service=self.service)
        if dlg.exec():
            s = dlg.get_settings()
            self._graph_range_preset = s["range_preset"]
            self._graph_view_mode = s["view_mode"]
            self._graph_hour_start = s["hour_start"]
            self._graph_hour_end = s["hour_end"]
            self._graph_custom_range = s["custom_range"]
            self._sync_graph_mode_buttons()
            self._reload_graphs()
