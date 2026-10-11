"""
SQLite persistence layer for JobTracker.

Tables:
  tasks      — timed subjects (name, color, notes, manual sort order)
  sessions   — task_id -> tasks.id, start/end times, duration, owning device
  todo_tasks — goals (legacy table name retained), completion and manual order
  milestones — ordered checklist items belonging to goals
  goal_templates — daily/weekly/monthly generators for normal goal instances
  settings   — key/value store for user preferences

Every shared table also carries a ``uid``: the row identity used when talking to
the server. Integer ids remain private to this database and never travel — see
``core/sync_policy.py``.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Union
from pathlib import Path

from .config import DB_PATH
from .models import Subject, Session, TodoTask, Milestone, GoalTemplate
from .persistence.connection import _AtomicConnection, open_connection, atomic as atomic_scope
from .persistence.schema import SchemaManager
from .persistence.identity import IdentityRepository
from .persistence.ordering import OrderingRepository
from .persistence.settings import SettingsRepository
from .persistence.subjects import SubjectRepository
from .persistence.sessions import SessionRepository
from .persistence.goals import GoalRepository
from .persistence.templates import TemplateRepository
from .persistence.backup import BackupCodec
from .persistence.weights import WeightRepository


class Database:
    def __init__(self, db_path: Optional[Union[str, Path]] = None) -> None:
        # ``db_path`` lets tests / tooling use an isolated database. Production
        # code uses the module-level ``db`` singleton, which defaults to the
        # configured DB_PATH (see config.py, also overridable via env var).
        path = str(db_path) if db_path is not None else str(DB_PATH)
        self.db_path = path
        self.connection = open_connection(path)
        self._schema = SchemaManager(self)
        self._identity = IdentityRepository(self)
        self._ordering = OrderingRepository(self)
        self._settings = SettingsRepository(self)
        self._subjects = SubjectRepository(self)
        self._sessions = SessionRepository(self)
        self._goals = GoalRepository(self)
        self._templates = TemplateRepository(self)
        self._backup = BackupCodec(self)
        self._weights = WeightRepository(self)
        self._init_db()

    def atomic(self):
        """Commit mutations and durable operations together on one connection.

        Nested calls use savepoints and intermediate method commits are deferred.
        """
        return atomic_scope(self.connection)

    # ── Schema ───────────────────────────────────────────────────────────
    def _init_db(self) -> None:
        return self._schema._init_db()

    def _column_exists(self, table: str, column: str) -> bool:
        return self._schema._column_exists(table, column)

    # ── uid ↔ local id (the network boundary) ────────────────────────────
    def id_for_uid(self, table: str, uid: str) -> Optional[int]:
        """Translate a wire identity into this database's local row id.

        Returns None when this database has never seen the uid — the caller
        decides whether that is a 404 or a row to create.
        """
        return self._identity.id_for_uid(table, uid)

    def uid_for_id(self, table: str, row_id: int) -> Optional[str]:
        """Translate a local row id into the identity other machines know."""
        return self._identity.uid_for_id(table, row_id)

    def _importable_uid(self, table: str, value) -> Optional[str]:
        """The uid to give an imported row, or None to let the trigger mint one.

        Backups carry uids so that restoring one keeps each row's cross-machine
        identity instead of silently forking it. A uid already used in this table
        is dropped rather than allowed to violate the unique index — the row
        still imports, it just gets a fresh identity.
        """
        return self._identity._importable_uid(table, value)

    def _next_sort_order(self, table: str) -> int:
        return self._ordering._next_sort_order(table)

    def _set_order(self, table: str, id_column: str, ordered_ids: list[int]) -> None:
        return self._ordering._set_order(table, id_column, ordered_ids)

    def _set_filtered_order(
        self,
        table: str,
        id_column: str,
        ordered_ids: list[int],
        where_clause: str,
        where_params: tuple,
    ) -> None:
        """Persist an order inside one filtered view without disturbing peers."""
        return self._ordering._set_filtered_order(table, id_column, ordered_ids, where_clause, where_params)

    # ── Settings ─────────────────────────────────────────────────────────
    def get_setting(self, key: str, default: str = "") -> str:
        return self._settings.get_setting(key, default)

    def set_setting(self, key: str, value: str) -> None:
        return self._settings.set_setting(key, value)

    def delete_setting(self, key: str) -> None:
        return self._settings.delete_setting(key)

    def get_or_create_device_id(self) -> str:
        """This install's stable identity, minted on first use.

        Used to decide who owns a running session: only the device that started
        one may auto-close it during crash recovery. Deliberately stored in
        ``settings`` and excluded from backups' restore path, so copying a
        database or a backup to another machine does not clone its identity.
        """
        return self._settings.get_or_create_device_id()

    # ── Subjects (timed) ─────────────────────────────────────────────────
    def add_subject(self, name: str, color: str, notes: str) -> Subject:
        return self._subjects.add_subject(name, color, notes)

    def update_subject(self, subject_id: int, name: str, color: str, notes: str) -> Optional[Subject]:
        return self._subjects.update_subject(subject_id, name, color, notes)

    def delete_subject(self, subject_id: int) -> None:
        return self._subjects.delete_subject(subject_id)

    def get_subject(self, subject_id: int) -> Optional[Subject]:
        return self._subjects.get_subject(subject_id)

    def get_subject_by_name(self, name: str) -> Optional[Subject]:
        return self._subjects.get_subject_by_name(name)

    def get_all_subjects(self, archived: bool = False) -> List[Subject]:
        return self._subjects.get_all_subjects(archived)

    def get_all_subjects_including_archived(self) -> List[Subject]:
        """Return every subject regardless of archive status (for graphs)."""
        return self._subjects.get_all_subjects_including_archived()

    def archive_subject(self, subject_id: int) -> None:
        return self._subjects.archive_subject(subject_id)

    def unarchive_subject(self, subject_id: int) -> None:
        return self._subjects.unarchive_subject(subject_id)

    def set_subject_order(
        self, ordered_ids: list[int], archived: bool = False
    ) -> None:
        return self._subjects.set_subject_order(ordered_ids, archived)

    # ── Sessions ─────────────────────────────────────────────────────────
    def start_session(
        self, subject_id: int, device_id: Optional[str] = None,
        start_time: Optional[datetime] = None,
    ) -> Session:
        return self._sessions.start_session(subject_id, device_id, start_time)

    def touch_active_session(self, session_id: int, when_iso: Optional[str] = None) -> None:
        """Update the 'last known active' timestamp for an open session.

        No-op if the session is already closed (``end_time`` set). This is the
        heartbeat write — one cheap UPDATE, no new rows, no history.
        """
        return self._sessions.touch_active_session(session_id, when_iso)

    def stop_session(
        self, session_id: int, end_time: datetime, duration_seconds: int
    ) -> None:
        return self._sessions.stop_session(session_id, end_time, duration_seconds)

    def close_recovered_session(
        self,
        session_id: int,
        end_time: datetime,
        duration_seconds: int,
    ) -> None:
        """Close an unfinished session without applying the 30-second deletion.

        This is only for automatic startup reconciliation of unexpected parallel
        open rows. Recovery must never silently delete unfinished user data.
        Normal user-initiated stop/add/edit operations still enforce the existing
        30-second minimum through :meth:`stop_session`.
        """
        return self._sessions.close_recovered_session(session_id, end_time, duration_seconds)

    def add_session(
        self,
        subject_id: int,
        start_time: datetime,
        end_time: datetime,
        duration_seconds: int,
    ) -> Optional[Session]:
        return self._sessions.add_session(subject_id, start_time, end_time, duration_seconds)

    def update_session(
        self,
        session_id: int,
        subject_id: int,
        start_time: datetime,
        end_time: datetime,
        duration_seconds: int,
    ) -> Optional[Session]:
        return self._sessions.update_session(session_id, subject_id, start_time, end_time, duration_seconds)

    def delete_session(self, session_id: int) -> None:
        return self._sessions.delete_session(session_id)

    def get_session(self, session_id: int) -> Optional[Session]:
        return self._sessions.get_session(session_id)

    def get_open_sessions(self) -> List[Session]:
        return self._sessions.get_open_sessions()

    def get_open_session(self) -> Optional[Session]:
        return self._sessions.get_open_session()

    def get_sessions_for_subject(self, subject_id: int) -> List[Session]:
        return self._sessions.get_sessions_for_subject(subject_id)

    def get_all_closed_sessions_in_range(
        self, since_iso: str, until_iso: str
    ) -> List[Session]:
        return self._sessions.get_all_closed_sessions_in_range(since_iso, until_iso)

    def get_earliest_session_date(self) -> Optional[str]:
        return self._sessions.get_earliest_session_date()

    def get_latest_session_date(self) -> Optional[str]:
        """Latest session START, including sessions scheduled ahead of now.

        Graph windows use this so a session created for a future day is
        actually visible instead of falling outside every range.
        """
        return self._sessions.get_latest_session_date()

    def get_subject_stats(self, subject_id: int, since_iso: Optional[str] = None) -> int:
        return self._sessions.get_subject_stats(subject_id, since_iso)

    def get_all_subject_stats(self, since_iso: Optional[str] = None) -> dict[int, int]:
        """Closed-session totals for every subject in one query.

        Replaces one aggregate query per subject card on every list reload.
        """
        return self._sessions.get_all_subject_stats(since_iso)

    def get_incomplete_todo_count(self) -> int:
        return self._goals.get_incomplete_todo_count()

    # ── Todo Tasks (completable) ─────────────────────────────────────────
    def _todo_order_mode(self) -> str:
        return self._goals._todo_order_mode()

    def _todo_ids_deadline_order(self) -> list[int]:
        return self._goals._todo_ids_deadline_order()

    def _ensure_manual_todo_order(self) -> None:
        return self._goals._ensure_manual_todo_order()

    def ensure_manual_goal_order(self) -> None:
        """Migrate the legacy deadline-sorted Tasks view to manual Goal order.

        The currently visible deadline order is materialised first, so the
        redesign does not unexpectedly scramble existing items.
        """
        return self._goals.ensure_manual_goal_order()

    def sort_todo_tasks_by_deadline(self) -> None:
        return self._goals.sort_todo_tasks_by_deadline()

    def add_todo_task(self, name: str, notes: str, deadline: Optional[str]) -> TodoTask:
        return self._goals.add_todo_task(name, notes, deadline)

    def update_todo_task(self, todo_task_id: int, name: str, notes: str, deadline: Optional[str]) -> Optional[TodoTask]:
        return self._goals.update_todo_task(todo_task_id, name, notes, deadline)

    def delete_todo_task(self, todo_task_id: int) -> None:
        return self._goals.delete_todo_task(todo_task_id)

    def get_todo_task(self, todo_task_id: int) -> Optional[TodoTask]:
        return self._goals.get_todo_task(todo_task_id)

    def get_all_todo_tasks(self) -> List[TodoTask]:
        return self._goals.get_all_todo_tasks()

    def get_completed_todo_tasks(self) -> List[TodoTask]:
        return self._goals.get_completed_todo_tasks()

    def add_todo_task_at_top(
        self, name: str, notes: str, template_id: Optional[int] = None
    ) -> TodoTask:
        """Insert a Goal at the TOP of the manual order (used by template
        generation). Forces manual ordering so the placement sticks."""
        return self._goals.add_todo_task_at_top(name, notes, template_id)

    def complete_todo_task(self, todo_task_id: int) -> None:
        return self._goals.complete_todo_task(todo_task_id)

    def uncomplete_todo_task(self, todo_task_id: int) -> None:
        """Reopen a completed Goal. Completion is always reversible."""
        return self._goals.uncomplete_todo_task(todo_task_id)

    def set_todo_task_focused(self, todo_task_id: int, focused: bool) -> None:
        return self._goals.set_todo_task_focused(todo_task_id, focused)

    def set_todo_task_order(
        self, ordered_ids: list[int], completed: bool = False
    ) -> None:
        return self._goals.set_todo_task_order(ordered_ids, completed)

    # ── Milestones ───────────────────────────────────────────────────────
    def add_milestone(self, goal_id: int, title: str, note: str = "") -> Optional[Milestone]:
        return self._goals.add_milestone(goal_id, title, note)

    def get_milestone(self, milestone_id: int) -> Optional[Milestone]:
        return self._goals.get_milestone(milestone_id)

    def get_milestones(self, goal_id: int) -> List[Milestone]:
        return self._goals.get_milestones(goal_id)

    def update_milestone(self, milestone_id: int, title: str, note: str = "") -> Optional[Milestone]:
        return self._goals.update_milestone(milestone_id, title, note)

    def set_milestone_done(self, milestone_id: int, done: bool) -> None:
        return self._goals.set_milestone_done(milestone_id, done)

    def delete_milestone(self, milestone_id: int) -> None:
        return self._goals.delete_milestone(milestone_id)

    def set_milestone_order(self, goal_id: int, ordered_ids: list[int]) -> None:
        return self._goals.set_milestone_order(goal_id, ordered_ids)

    # ── Goal templates (recurring) ───────────────────────────────────────
    def add_goal_template(
        self, title: str, notes: str, recurrence: str, milestones_json: str = "[]",
        recurrence_day: Optional[int] = None,
    ) -> Optional[GoalTemplate]:
        return self._templates.add_goal_template(title, notes, recurrence, milestones_json, recurrence_day)

    def get_goal_template(self, template_id: int) -> Optional[GoalTemplate]:
        return self._templates.get_goal_template(template_id)

    def get_goal_templates(self, active_only: bool = False) -> List[GoalTemplate]:
        return self._templates.get_goal_templates(active_only)

    def update_goal_template(
        self, template_id: int, title: str, notes: str, recurrence: str,
        milestones_json: str, recurrence_day: Optional[int] = None,
    ) -> Optional[GoalTemplate]:
        return self._templates.update_goal_template(template_id, title, notes, recurrence, milestones_json, recurrence_day)

    @staticmethod
    def _normalize_recurrence_day(
        recurrence: str, recurrence_day: Optional[int]
    ) -> Optional[int]:
        return TemplateRepository._normalize_recurrence_day(recurrence, recurrence_day)

    def set_goal_template_active(self, template_id: int, active: bool) -> None:
        return self._templates.set_goal_template_active(template_id, active)

    def delete_goal_template(self, template_id: int) -> None:
        return self._templates.delete_goal_template(template_id)

    def set_goal_template_last_generated(self, template_id: int, period_key: str) -> None:
        return self._templates.set_goal_template_last_generated(template_id, period_key)

    # ── Backup / Restore ─────────────────────────────────────────────────
    def export_data(self) -> dict:
        return self._backup.export_data()

    def import_data(self, data: dict) -> None:
        """Atomically merge an authoritative JSON backup.

        A malformed row must not leave a half-imported database that gets
        committed by a later unrelated action. A savepoint works both when the
        connection is idle and when a caller already has a transaction open.
        """
        return self._backup.import_data(data)

    def _import_data_rows(self, data: dict) -> None:
        return self._backup._import_data_rows(data)


# Shared singleton — production code imports this. Tests construct their own
# Database(tmp_path) instead and never rely on this object.
db = Database()
