from dataclasses import dataclass
from typing import Optional


@dataclass
class Subject:
    id: Optional[int]
    name: str
    color: str
    notes: str
    created_at: str
    sort_order: int = 0
    is_archived: int = 0


@dataclass
class TodoTask:
    id: Optional[int]
    name: str
    notes: str
    deadline: Optional[str]
    is_completed: int
    sort_order: int
    created_at: str


@dataclass
class Session:
    id: Optional[int]
    task_id: int
    start_time: str
    end_time: Optional[str]
    duration_seconds: int
    note: Optional[str] = None
    # Last time the app confirmed this session was still legitimately running.
    # Heartbeated ~once per minute while active; used by future crash/ghost-time
    # recovery. None for historical rows and for imported backups.
    last_active_at: Optional[str] = None

    @property
    def subject_id(self) -> int:
        """Semantic alias for task_id used by newer subject-oriented code paths."""
        return self.task_id

    @subject_id.setter
    def subject_id(self, value: int) -> None:
        self.task_id = int(value)
