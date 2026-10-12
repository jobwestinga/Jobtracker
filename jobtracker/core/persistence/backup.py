"""Authoritative backup serialization and atomic merge."""

from __future__ import annotations

import logging
from datetime import datetime

from .. import sync_policy

logger = logging.getLogger("jobtracker")


class BackupCodec:
    """Authoritative backup serialization and atomic merge."""

    def __init__(self, database):
        self._database = database

    def export_data(self) -> dict:
        # Separate sync connections may commit between table reads. A read
        # savepoint keeps related rows in one snapshot without blocking WAL
        # writers or committing a transaction already owned by the caller.
        connection = self._database.connection
        connection.execute("SAVEPOINT jobtracker_export")
        try:
            exported = self._export_rows()
            connection.execute("RELEASE SAVEPOINT jobtracker_export")
            return exported
        except BaseException:
            connection.execute("ROLLBACK TO SAVEPOINT jobtracker_export")
            connection.execute("RELEASE SAVEPOINT jobtracker_export")
            raise

    def _export_rows(self) -> dict:
        cur = self._database.connection.cursor()
        cur.execute("SELECT * FROM tasks")
        subjects = [dict(row) for row in cur.fetchall()]

        cur.execute("SELECT * FROM sessions")
        sessions = [dict(row) for row in cur.fetchall()]

        cur.execute("SELECT * FROM todo_tasks")
        todo_tasks = [dict(row) for row in cur.fetchall()]

        cur.execute("SELECT * FROM milestones")
        milestones = [dict(row) for row in cur.fetchall()]

        cur.execute("SELECT * FROM goal_templates")
        goal_templates = [dict(row) for row in cur.fetchall()]

        cur.execute("SELECT * FROM settings")
        settings = [dict(row) for row in cur.fetchall()]

        cur.execute("SELECT * FROM weight_measurements ORDER BY measured_at")
        weights = [dict(row) for row in cur.fetchall()]
        cur.execute("SELECT alias_uid, weight_uid FROM weight_uid_aliases")
        weight_aliases = [dict(row) for row in cur.fetchall()]

        logger.info(
            "Exported %d subjects, %d sessions, %d goals, %d milestones, "
            "%d templates, %d settings",
            len(subjects), len(sessions), len(todo_tasks), len(milestones),
            len(goal_templates), len(settings),
        )
        return {
            "subjects": subjects,
            "sessions": sessions,
            "todo_tasks": todo_tasks,
            "milestones": milestones,
            "goal_templates": goal_templates,
            "settings": settings,
            "weights": weights,
            "weight_aliases": weight_aliases,
        }

    def import_data(self, data: dict) -> None:
        """Atomically merge an authoritative JSON backup.

        A malformed row must not leave a half-imported database that gets
        committed by a later unrelated action. A savepoint works both when the
        connection is idle and when a caller already has a transaction open.
        """
        cursor = self._database.connection.cursor()
        cursor.execute("SAVEPOINT jobtracker_import")
        try:
            self._database._import_data_rows(data)
            cursor.execute("RELEASE SAVEPOINT jobtracker_import")
        except Exception:
            cursor.execute("ROLLBACK TO SAVEPOINT jobtracker_import")
            cursor.execute("RELEASE SAVEPOINT jobtracker_import")
            logger.exception("Backup import rolled back")
            raise

    def _import_data_rows(self, data: dict) -> None:
        cur = self._database.connection.cursor()

        # Import subjects (legacy key: tasks)
        subjects = data.get("subjects", data.get("tasks", []))
        subject_id_map: dict[int, int] = {}

        for subject in subjects:
            if not subject.get("name"):
                continue
            legacy_id = subject.get("id")
            cur.execute("SELECT id FROM tasks WHERE name = ? COLLATE NOCASE", (subject["name"],))
            existing = cur.fetchone()
            if existing:
                if legacy_id is not None:
                    try:
                        subject_id_map[int(legacy_id)] = int(existing["id"])
                    except (TypeError, ValueError):
                        pass
                continue

            cur.execute(
                "INSERT INTO tasks "
                "(name, color, notes, sort_order, is_archived, created_at, uid) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    subject["name"],
                    subject.get("color", "#3B82F6"),
                    subject.get("notes", ""),
                    subject.get("sort_order", self._database._next_sort_order("tasks")),
                    subject.get("is_archived", 0),
                    subject.get("created_at", datetime.now().isoformat()),
                    self._database._importable_uid("tasks", subject.get("uid")),
                ),
            )
            if legacy_id is not None:
                try:
                    subject_id_map[int(legacy_id)] = int(cur.lastrowid)
                except (TypeError, ValueError):
                    pass

        for sess in data.get("sessions", []):
            legacy_subject_id = sess.get("task_id", sess.get("subject_id"))
            if legacy_subject_id is None or not sess.get("start_time"):
                continue
            try:
                legacy_subject_id = int(legacy_subject_id)
            except (TypeError, ValueError):
                continue
            new_subject_id = subject_id_map.get(legacy_subject_id)
            if not new_subject_id:
                continue
            cur.execute(
                "SELECT id FROM sessions WHERE task_id = ? AND start_time = ? "
                "AND COALESCE(end_time, '') = COALESCE(?, '')",
                (new_subject_id, sess["start_time"], sess.get("end_time")),
            )
            if cur.fetchone():
                continue
            # Older backups may still carry a "note" key; it is ignored on
            # purpose since session notes were removed from the schema.
            cur.execute(
                "INSERT INTO sessions "
                "(task_id, start_time, end_time, duration_seconds, last_active_at, "
                "device_id, uid) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    new_subject_id,
                    sess["start_time"],
                    sess.get("end_time"),
                    sess.get("duration_seconds", 0),
                    sess.get("last_active_at"),
                    sess.get("device_id"),
                    self._database._importable_uid("sessions", sess.get("uid")),
                ),
            )

        # Import recurring templates first so generated goal instances can retain
        # their template association after IDs are remapped.
        template_id_map: dict[int, int] = {}
        for tpl in data.get("goal_templates", []):
            if not tpl.get("title"):
                continue
            legacy_template_id = tpl.get("id")
            recurrence = tpl.get("recurrence", "daily")
            cur.execute(
                "SELECT id FROM goal_templates "
                "WHERE title = ? COLLATE NOCASE AND recurrence = ? "
                "AND COALESCE(recurrence_day, 0) = COALESCE(?, 0)",
                (
                    tpl["title"],
                    recurrence,
                    self._database._normalize_recurrence_day(
                        recurrence, tpl.get("recurrence_day")
                    ),
                ),
            )
            existing = cur.fetchone()
            if existing:
                new_template_id = int(existing["id"])
            else:
                cur.execute(
                    "INSERT INTO goal_templates "
                    "(title, notes, recurrence, recurrence_day, milestones_json, "
                    "last_generated, is_active, sort_order, created_at, uid) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        tpl["title"],
                        tpl.get("notes", ""),
                        recurrence,
                        self._database._normalize_recurrence_day(
                            recurrence, tpl.get("recurrence_day")
                        ),
                        tpl.get("milestones_json", "[]"),
                        tpl.get("last_generated"),
                        tpl.get("is_active", 1),
                        tpl.get("sort_order", self._database._next_sort_order("goal_templates")),
                        tpl.get("created_at", datetime.now().isoformat()),
                        self._database._importable_uid("goal_templates", tpl.get("uid")),
                    ),
                )
                new_template_id = int(cur.lastrowid)
            if legacy_template_id is not None:
                try:
                    template_id_map[int(legacy_template_id)] = new_template_id
                except (TypeError, ValueError):
                    pass

        # Import goals (legacy table name: todo_tasks); track ID mapping for
        # milestones. Created-at + sort-order are included in modern backup
        # identity so repeated generated goals with the same title are preserved.
        goal_id_map: dict[int, int] = {}
        for item in data.get("todo_tasks", []):
            if not item.get("name"):
                continue
            legacy_goal_id = item.get("id")
            created_at = item.get("created_at")
            sort_order = item.get("sort_order")
            if created_at is not None and sort_order is not None:
                cur.execute(
                    "SELECT id FROM todo_tasks WHERE name = ? COLLATE NOCASE "
                    "AND COALESCE(deadline, '') = COALESCE(?, '') "
                    "AND created_at = ? AND sort_order = ?",
                    (item["name"], item.get("deadline"), created_at, sort_order),
                )
            else:
                # Legacy payloads lacked reliable instance identity.
                cur.execute(
                    "SELECT id FROM todo_tasks WHERE name = ? COLLATE NOCASE "
                    "AND COALESCE(deadline, '') = COALESCE(?, '')",
                    (item["name"], item.get("deadline")),
                )
            existing = cur.fetchone()
            if existing:
                if legacy_goal_id is not None:
                    try:
                        goal_id_map[int(legacy_goal_id)] = int(existing["id"])
                    except (TypeError, ValueError):
                        pass
                continue
            legacy_template_id = item.get("template_id")
            try:
                new_template_id = template_id_map.get(int(legacy_template_id))
            except (TypeError, ValueError):
                new_template_id = None
            cur.execute(
                "INSERT INTO todo_tasks "
                "(name, notes, deadline, is_completed, sort_order, created_at, "
                "template_id, is_focused, uid) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    item["name"],
                    item.get("notes", ""),
                    item.get("deadline"),
                    item.get("is_completed", 0),
                    item.get("sort_order", self._database._next_sort_order("todo_tasks")),
                    item.get("created_at", datetime.now().isoformat()),
                    new_template_id,
                    item.get("is_focused", 0),
                    self._database._importable_uid("todo_tasks", item.get("uid")),
                ),
            )
            if legacy_goal_id is not None:
                try:
                    goal_id_map[int(legacy_goal_id)] = int(cur.lastrowid)
                except (TypeError, ValueError):
                    pass

        # Import milestones, remapping goal_id onto the imported goals.
        for ms in data.get("milestones", []):
            legacy_goal = ms.get("goal_id")
            if legacy_goal is None or not ms.get("title"):
                continue
            try:
                new_goal_id = goal_id_map.get(int(legacy_goal))
            except (TypeError, ValueError):
                new_goal_id = None
            if not new_goal_id:
                continue
            cur.execute(
                "SELECT id FROM milestones WHERE goal_id = ? "
                "AND title = ? COLLATE NOCASE AND sort_order = ?",
                (new_goal_id, ms["title"], ms.get("sort_order", 0)),
            )
            if cur.fetchone():
                continue
            cur.execute(
                "INSERT INTO milestones "
                "(goal_id, title, note, is_done, sort_order, created_at, uid) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    new_goal_id,
                    ms["title"],
                    ms.get("note", ""),
                    ms.get("is_done", 0),
                    ms.get("sort_order", 0),
                    ms.get("created_at", datetime.now().isoformat()),
                    self._database._importable_uid("milestones", ms.get("uid")),
                ),
            )

        # Weights are independent rows with identities preserved on restore.
        # Validation and duplicate handling use the same rules as source imports.
        self._database._weights.import_rows(data.get("weights", []))
        for alias in data.get("weight_aliases", []):
            if not isinstance(alias, dict):
                raise ValueError("Invalid weight identity alias")
            alias_uid, weight_uid = alias.get("alias_uid"), alias.get("weight_uid")
            if not all(isinstance(uid, str) and uid for uid in (alias_uid, weight_uid)):
                raise ValueError("Invalid weight identity alias")
            if self._database.id_for_uid("weight_measurements", weight_uid) is None:
                continue
            canonical_id = self._database.id_for_uid("weight_measurements", alias_uid)
            if canonical_id is not None and alias_uid != weight_uid:
                raise ValueError("Weight identity alias conflicts with an existing measurement")
            existing = cur.execute(
                "SELECT weight_uid FROM weight_uid_aliases WHERE alias_uid = ?", (alias_uid,)
            ).fetchone()
            if existing is not None and existing["weight_uid"] != weight_uid:
                raise ValueError("Weight identity alias refers to different entries")
            cur.execute(
                "INSERT INTO weight_uid_aliases (alias_uid, weight_uid) VALUES (?, ?) "
                "ON CONFLICT(alias_uid) DO NOTHING", (alias_uid, weight_uid)
            )

        # Settings are part of the authoritative JSON backup. Unknown keys are
        # intentionally retained for forward compatibility.
        for setting in data.get("settings", []):
            key = setting.get("key")
            value = setting.get("value")
            if not isinstance(key, str) or not isinstance(value, str) or not key:
                continue
            if key in sync_policy.DEVICE_LOCAL_SETTING_KEYS:
                # Restoring a backup must not make this machine claim another
                # machine's identity — two installs sharing a device_id would
                # both think they own the same running session.
                continue
            cur.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

        logger.info(
            "Imported backup: %d subject(s), %d session(s), %d goal(s), "
            "%d milestone(s), %d template(s), %d setting(s)",
            len(subjects), len(data.get("sessions", [])), len(data.get("todo_tasks", [])),
            len(data.get("milestones", [])), len(data.get("goal_templates", [])),
            len(data.get("settings", [])),
        )
