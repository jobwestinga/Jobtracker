"""A repeatable synthetic store for Qt/native comparisons and engine tests.

Everything is anchored on a fixed clock and every row receives a stable UID, so
the same data renders identically in both frontends and across runs. It covers
long names, archived subjects, reordered goals, a three-milestone goal,
recurring templates, dense, future, overlapping and fractional sessions, an
owned running timer, an optional foreign-device timer and imported weights.

    python -m tests.support.synthetic_store /tmp/a4/synthetic.db [--now ISO] [--empty]
"""
from __future__ import annotations

import argparse
import os
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

FIXED_NOW = datetime(2026, 10, 21, 16, 20, 0)
NAMESPACE = uuid.UUID("6f1f3d4e-2a5b-4c1a-9a6e-0a4a4a4a4a4a")

SUBJECTS = [
    ("Physics", "#3B82F6", "Mechanics and waves", False),
    ("Linear Algebra and a deliberately long subject name for elision", "#22C55E", "", False),
    ("Thesis writing", "#A855F7", "Chapter drafts", False),
    ("Reading", "#F59E0B", "", False),
    ("Spanish", "#EF4444", "", False),
    ("Old project", "#64748B", "Archived history", True),
]


def stable_uid(*parts) -> str:
    return str(uuid.uuid5(NAMESPACE, "/".join(str(p) for p in parts)))


def _set_uid(db, table: str, row_id: int, uid: str) -> None:
    db.connection.execute(f"UPDATE {table} SET uid = ? WHERE id = ?", (uid, row_id))


def build(db_path, *, now: datetime = FIXED_NOW, empty: bool = False, foreign_timer: bool = False,
          running_timer: bool = True, theme: tuple[str, str] = ("Clean", "Ocean")) -> dict:
    """Create the store at ``db_path`` (must not exist). Returns key UIDs."""
    from jobtracker.core.database import Database
    from jobtracker.services.tracker_service import TrackerService

    path = Path(db_path)
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = Database(path)
    try:
        db.set_setting("device_id", stable_uid("device", "mac"))
        service = TrackerService(db, recover=False)
        db.set_setting("theme_fx", theme[0])
        db.set_setting("theme_palette", theme[1])
        if empty:
            db.connection.commit()
            return {"subjects": {}}
        uids: dict = {"subjects": {}, "goals": {}, "sessions": []}
        ids = {}
        for name, color, notes, archived in SUBJECTS:
            subject = service.add_subject(name, color, notes)
            ids[name] = subject.id
            uid = stable_uid("subject", name)
            _set_uid(db, "tasks", subject.id, uid)
            uids["subjects"][name] = uid
            if archived:
                service.archive_subject(subject.id)

        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        plan = [  # (days ago, subject, start hour, minutes)
            (0, "Physics", 9.0, 95), (0, "Thesis writing", 11.5, 120), (0, "Reading", 14.25, 45),
            (1, "Physics", 8.0, 180), (1, "Linear Algebra and a deliberately long subject name for elision", 13.0, 240),
            (1, "Spanish", 19.0, 30), (1, "Reading", 22.5, 150),  # crosses midnight -> logical day
            (2, "Thesis writing", 9.0, 300), (2, "Physics", 15.0, 175), (2, "Spanish", 20.0, 45),
            (3, "Physics", 10.0, 474), (4, "Reading", 9.5, 60), (5, "Thesis writing", 8.5, 660),
            (6, "Spanish", 18.0, 25), (8, "Old project", 10.0, 90), (12, "Physics", 9.0, 300),
            (20, "Thesis writing", 9.0, 420), (35, "Reading", 20.0, 120), (70, "Physics", 9.0, 600),
        ]
        for index, (ago, name, hour, minutes) in enumerate(plan):
            start = day - timedelta(days=ago) + timedelta(hours=hour)
            if index == 2:
                start += timedelta(microseconds=250000)  # fractional endpoint precision
            session = service.add_session(ids[name], start, start + timedelta(minutes=minutes))
            uid = stable_uid("session", index)
            _set_uid(db, "sessions", session.id, uid)
            uids["sessions"].append(uid)
        # Dense: many short adjacent blocks on one day.
        dense_day = day - timedelta(days=9) + timedelta(hours=8)
        for block in range(14):
            start = dense_day + timedelta(minutes=35 * block)
            session = service.add_session(ids["Physics" if block % 2 else "Reading"], start, start + timedelta(minutes=33))
            _set_uid(db, "sessions", session.id, stable_uid("dense", block))
        # Overlapping and future history are valid and must stay untouched.
        overlap = day - timedelta(days=2) + timedelta(hours=10)
        session = service.add_session(ids["Reading"], overlap, overlap + timedelta(minutes=50))
        _set_uid(db, "sessions", session.id, stable_uid("overlap"))
        future = day + timedelta(days=2, hours=10)
        session = service.add_session(ids["Spanish"], future, future + timedelta(minutes=40))
        _set_uid(db, "sessions", session.id, stable_uid("future"))

        goals = [
            ("Submit thesis chapter", "Draft, revise and submit chapter three.", "2026-11-15"),
            ("Run 10k", "", None),
            ("Read four books", "One per week of the month.", None),
            ("Renew passport", "", "2026-12-01"),
            ("Learn 200 Spanish words", "", None),
            ("Plan winter trip", "", None),
        ]
        goal_ids = []
        for title, notes, deadline in goals:
            goal = service.add_todo_task(title, notes, deadline)
            _set_uid(db, "todo_tasks", goal.id, stable_uid("goal", title))
            uids["goals"][title] = stable_uid("goal", title)
            goal_ids.append(goal.id)
        for index, title in enumerate(("Outline", "First draft", "Revision")):
            milestone = service.add_milestone(goal_ids[0], title, "")
            _set_uid(db, "milestones", milestone.id, stable_uid("milestone", index))
            if index == 0:
                service.set_milestone_done(milestone.id, True)
        service.toggle_goal_focused(goal_ids[0])
        service.set_todo_task_order([goal_ids[i] for i in (0, 2, 1, 4, 3, 5)])
        service.complete_goal(goal_ids[5])
        template = service.add_goal_template("Weekly review {date}", "", "weekly", ["Inbox zero", "Plan week"], 1)
        _set_uid(db, "goal_templates", template.id, stable_uid("template", "weekly"))

        if running_timer:
            service.start_subject(ids["Physics"], start_time=now - timedelta(minutes=25))
            service.heartbeat_active_session(now - timedelta(seconds=30))
            _set_uid(db, "sessions", service.active_session.id, stable_uid("running"))
        if foreign_timer:
            phone = TrackerService(db, device_id=stable_uid("device", "phone"), recover=False)
            phone.start_subject(ids["Spanish"], start_time=now - timedelta(hours=1), allow_parallel=True)

        weights = []
        for offset in range(0, 120, 3):
            stamp = now - timedelta(days=offset, hours=8, minutes=offset % 7, seconds=offset % 11)
            weights.append({"measured_at": stamp.isoformat(), "weight_kg": round(81.4 - offset * 0.031 + (offset % 5) * 0.137, 3), "source": "scale"})
        db._weights.import_rows(weights)
        service.upsert_weight(now.replace(hour=7, minute=30).isoformat(), 78.6)
        db.connection.commit()
        return uids
    finally:
        db.connection.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path")
    parser.add_argument("--now", default=FIXED_NOW.isoformat())
    parser.add_argument("--empty", action="store_true")
    parser.add_argument("--foreign-timer", action="store_true")
    parser.add_argument("--no-timer", action="store_true")
    args = parser.parse_args(argv)
    path = Path(args.path).expanduser().resolve()
    os.environ.setdefault("JOBTRACKER_DB_PATH", str(path.with_name(path.stem + "-singleton.db")))
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    build(path, now=datetime.fromisoformat(args.now), empty=args.empty,
          foreign_timer=args.foreign_timer, running_timer=not args.no_timer)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
