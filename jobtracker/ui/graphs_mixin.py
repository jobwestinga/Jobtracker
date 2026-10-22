"""
Graphs page: stacked bar chart and agenda timeline, reload, and settings.

Mixed into MainWindow. Relies on shared attributes/methods of the host window:
``self.service``, ``self._pages``, ``self._open_settings`` and the persisted graph
state (``self._graph_*``).
"""

from __future__ import annotations

import logging
from datetime import date
from html import escape

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QStackedWidget, QVBoxLayout,
    QWidget,
)

from ..core.timeutils import agenda_hour_label
from ..services import graph_window
from .widgets.agenda_view import AgendaViewWidget
from .widgets.day_sessions_dialog import DaySessionsDialog
from .widgets.dialog_utils import open_dialog
from .widgets.graph_settings_dialog import GraphSettingsDialog
from .widgets.graphs_view import WorkGraphWidget
from .widgets import session_list

logger = logging.getLogger("jobtracker")


class GraphsMixin:
    # ── persisted-setting loaders (used by MainWindow.__init__) ─────────
    def _load_graph_range_preset(self) -> str:
        return graph_window.load_range_preset(self.service.get_setting)

    def _load_custom_range(self) -> tuple[date, date] | None:
        return graph_window.load_custom_range(self.service.get_setting)

    def _load_graph_grouping(self) -> str:
        """Persisted bucket-size override; "auto" means follow the range."""
        return graph_window.load_grouping(self.service.get_setting)

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

        lay.addLayout(header)

        view_controls = QHBoxLayout()
        view_controls.setSpacing(7)

        self._graph_mode_buttons: dict[str, QPushButton] = {}
        for label, mode, arrow in (
            ("Stacked Bar", "bar", "Up"),
            ("Agenda", "agenda", "Down"),
        ):
            button = QPushButton(label)
            button.setMinimumHeight(34)
            button.setCursor(Qt.PointingHandCursor)
            button.setToolTip(
                f"Press {arrow} to switch to {label}"
            )
            button.clicked.connect(
                lambda _checked=False, selected=mode: self._queue_ui_action(
                    self._set_graph_view_mode, selected
                )
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
        self._graph_view.day_clicked.connect(self._open_day_sessions)
        self._graph_stack.addWidget(self._graph_view)  # index 0

        self._agenda_view = AgendaViewWidget()
        self._agenda_view.day_clicked.connect(self._open_day_sessions)
        self._agenda_view.session_clicked.connect(self._open_session_editor)
        self._graph_stack.addWidget(self._agenda_view)  # index 1

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
        else:
            self._reload_bar_chart()

    def _set_graph_view_mode(self, mode: str) -> None:
        if mode not in {"bar", "agenda"}:
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

    def _graph_preferences(self) -> graph_window.GraphPreferences:
        return graph_window.GraphPreferences(
            preset=self._graph_range_preset,
            custom_range=self._graph_custom_range,
            grouping=getattr(self, "_graph_grouping", "auto"),
            hour_start=self._graph_hour_start,
            hour_end=self._graph_hour_end,
            autofit_hours=self.service.get_setting("graph_autofit_hours", "0") == "1",
        )

    def _window_and_grouping(self) -> tuple[date | None, date | None, str]:
        """(start_date, end_date, grouping). Dates are None for all-history; the
        grouping (daily/weekly/monthly) is derived from the chosen range."""
        return graph_window.window_and_grouping(self.service, self._graph_preferences())

    def _range_label(self) -> str:
        return graph_window.range_label(self._graph_preferences())

    def _set_legend(self, seen: dict[str, str]) -> None:
        self._graph_legend.show()
        if seen:
            parts = [
                f"<span style='white-space: nowrap;'><span style='color:{color};'>●</span>&nbsp;{escape(name)}</span>"
                for name, color in seen.items()
            ]
            self._graph_legend.setText("&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;".join(parts))
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

        agenda = graph_window.agenda_window(self.service, self._graph_preferences())
        day_keys, sessions = agenda.day_keys, agenda.sessions
        hour_start, hour_end = agenda.hour_start, agenda.hour_end

        fit_width = self.service.get_setting("graph_fit_horizontal", "1") == "1"
        self._agenda_view.set_data(
            sessions, day_keys, hour_start, hour_end, fit_width, now_marker=agenda.now_marker
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

    def _open_day_sessions(self, day_iso: str) -> None:
        """Inspect one logical day's sessions (from the bar chart or agenda)."""
        try:
            day = date.fromisoformat(day_iso)
        except ValueError:
            logger.warning("Ignored invalid graph day: %r", day_iso)
            return
        dialog = DaySessionsDialog(self.service, day, self)
        open_dialog(
            dialog,
            lambda _result, _dialog: self._finish_day_sessions(),
        )

    def _finish_day_sessions(self) -> None:
        self._reload_subjects()
        self._reload_graphs()

    def _open_session_editor(self, session_id: int) -> None:
        """Edit (or delete) exactly the agenda block that was clicked.

        Goes through the shared editor so the agenda gets the same subject
        move, nudge, and delete affordances as the session lists.
        """
        session_list.edit_session(
            self, self.service, session_id, self._finish_day_sessions
        )

    def _refresh_graphs_if_needed(self) -> None:
        if self.service.active_session and self._pages.currentIndex() == 2:
            self._reload_graphs()

    def _open_graph_settings(self) -> None:
        dlg = GraphSettingsDialog(self, service=self.service)
        open_dialog(dlg, self._finish_graph_settings)

    def _finish_graph_settings(self, result: int, dialog: QDialog) -> None:
        if result != QDialog.Accepted:
            return
        s = dialog.get_settings()
        self._graph_range_preset = s["range_preset"]
        self._graph_grouping = s.get("grouping", "auto")
        self._graph_view_mode = s["view_mode"]
        self._graph_hour_start = s["hour_start"]
        self._graph_hour_end = s["hour_end"]
        self._graph_custom_range = s["custom_range"]
        self._sync_graph_mode_buttons()
        self._reload_graphs()
