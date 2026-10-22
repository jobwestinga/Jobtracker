"""Subject SQL uses the legacy tasks table unchanged."""

from __future__ import annotations

from typing import List, Optional

from ..models import Subject


class SubjectRepository:
    """Subject SQL uses the legacy tasks table unchanged."""

    def __init__(self, database):
        self._database = database

    def add_subject(self, name: str, color: str, notes: str) -> Subject:
        normalized_name = (name or "").strip()
        if not normalized_name:
            raise ValueError("Subject name cannot be empty")

        cur = self._database.connection.cursor()
        cur.execute(
            "INSERT INTO tasks (name, color, notes, sort_order) VALUES (?, ?, ?, ?)",
            (
                normalized_name,
                (color or "").strip() or "#3B82F6",
                (notes or "").strip(),
                self._database._next_sort_order("tasks"),
            ),
        )
        self._database.connection.commit()
        return self._database.get_subject(cur.lastrowid)

    def update_subject(self, subject_id: int, name: str, color: str, notes: str) -> Optional[Subject]:
        normalized_name = (name or "").strip()
        if not normalized_name:
            return None

        cur = self._database.connection.cursor()
        cur.execute(
            "UPDATE tasks SET name = ?, color = ?, notes = ? WHERE id = ?",
            (normalized_name, (color or "").strip() or "#3B82F6", (notes or "").strip(), subject_id),
        )
        self._database.connection.commit()
        return self._database.get_subject(subject_id)

    def delete_subject(self, subject_id: int) -> None:
        cur = self._database.connection.cursor()
        cur.execute("DELETE FROM tasks WHERE id = ?", (subject_id,))
        self._database.connection.commit()

    def get_subject(self, subject_id: int) -> Optional[Subject]:
        cur = self._database.connection.cursor()
        cur.execute("SELECT * FROM tasks WHERE id = ?", (subject_id,))
        row = cur.fetchone()
        return Subject(**dict(row)) if row else None

    def get_subject_by_name(self, name: str) -> Optional[Subject]:
        cur = self._database.connection.cursor()
        cur.execute("SELECT * FROM tasks WHERE name = ? COLLATE NOCASE", (name,))
        row = cur.fetchone()
        return Subject(**dict(row)) if row else None

    def get_all_subjects(self, archived: bool = False) -> List[Subject]:
        cur = self._database.connection.cursor()
        cur.execute(
            "SELECT * FROM tasks WHERE is_archived = ? ORDER BY sort_order ASC, created_at DESC",
            (1 if archived else 0,),
        )
        return [Subject(**dict(row)) for row in cur.fetchall()]

    def get_all_subjects_including_archived(self) -> List[Subject]:
        """Return every subject regardless of archive status (for graphs)."""
        cur = self._database.connection.cursor()
        cur.execute("SELECT * FROM tasks ORDER BY sort_order ASC, created_at DESC")
        return [Subject(**dict(row)) for row in cur.fetchall()]

    def get_last_tracked_subject(self, before: str) -> Optional[Subject]:
        """Latest resumable work, independent of subject order or manual adds.

        Device-owned sessions identify actual timers. Older imports may not
        have an owner, so use their history only when no timed row is present.
        Future history remains valid data but does not describe recent work.
        """
        row = self._database.connection.execute(
            "SELECT t.* FROM tasks AS t JOIN sessions AS s ON s.task_id = t.id "
            "WHERE t.is_archived = 0 AND s.start_time <= ? "
            "AND COALESCE(s.end_time, s.last_active_at, s.start_time) <= ? "
            "ORDER BY (s.device_id IS NOT NULL) DESC, "
            "COALESCE(s.end_time, s.last_active_at, s.start_time) DESC, s.id DESC LIMIT 1",
            (before, before),
        ).fetchone()
        return Subject(**dict(row)) if row else None

    def archive_subject(self, subject_id: int) -> None:
        cur = self._database.connection.cursor()
        cur.execute("UPDATE tasks SET is_archived = 1 WHERE id = ?", (subject_id,))
        self._database.connection.commit()

    def unarchive_subject(self, subject_id: int) -> None:
        cur = self._database.connection.cursor()
        cur.execute("UPDATE tasks SET is_archived = 0 WHERE id = ?", (subject_id,))
        self._database.connection.commit()

    def set_subject_order(
        self, ordered_ids: list[int], archived: bool = False
    ) -> None:
        if not ordered_ids:
            return
        self._database._set_filtered_order(
            "tasks",
            "id",
            ordered_ids,
            "is_archived = ?",
            (1 if archived else 0,),
        )
