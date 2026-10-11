"""Subject validation, archive and ordering use cases."""

from __future__ import annotations

from typing import List, Optional

from ..core.models import Subject


class SubjectService:
    """Subject use cases; nested timer actions dispatch through the facade."""

    def __init__(self, tracker):
        self._tracker = tracker

    def get_all_subjects(self, archived: bool = False) -> List[Subject]:
        return self._tracker.db.get_all_subjects(archived=archived)

    def get_all_subjects_including_archived(self) -> List[Subject]:
        return self._tracker.db.get_all_subjects_including_archived()

    def add_subject(self, name: str, color: str, notes: str) -> Optional[Subject]:
        normalized_name = (name or "").strip()
        normalized_notes = (notes or "").strip()
        normalized_color = (color or "").strip() or "#3B82F6"
        if not normalized_name:
            return None
        if self._tracker.db.get_subject_by_name(normalized_name):
            return None
        return self._tracker.db.add_subject(normalized_name, normalized_color, normalized_notes)

    def update_subject(self, subject_id: int, name: str, color: str, notes: str) -> Optional[Subject]:
        normalized_name = (name or "").strip()
        normalized_notes = (notes or "").strip()
        normalized_color = (color or "").strip() or "#3B82F6"
        if not normalized_name:
            return None
        existing = self._tracker.db.get_subject_by_name(normalized_name)
        if existing and existing.id != subject_id:
            return None
        return self._tracker.db.update_subject(subject_id, normalized_name, normalized_color, normalized_notes)

    def delete_subject(self, subject_id: int) -> None:
        # Stop the timer before deleting the subject row.
        if self._tracker.active_subject and self._tracker.active_subject.id == subject_id:
            self._tracker.stop_active_subject()
        self._tracker.db.delete_subject(subject_id)  # CASCADE deletes sessions too

    def set_subject_order(
        self, ordered_ids: list[int], archived: bool = False
    ) -> None:
        self._tracker.db.set_subject_order(ordered_ids, archived=archived)

    def archive_subject(self, subject_id: int) -> None:
        if self._tracker.active_subject and self._tracker.active_subject.id == subject_id:
            self._tracker.stop_active_subject()
        self._tracker.db.archive_subject(subject_id)

    def unarchive_subject(self, subject_id: int) -> None:
        self._tracker.db.unarchive_subject(subject_id)
