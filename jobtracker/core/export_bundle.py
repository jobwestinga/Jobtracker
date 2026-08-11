"""
Export bundle builders (pure, UI-free).

Produces a human-readable backup bundle:
  • jobtracker_backup.json  — authoritative full backup (unchanged restore format)
  • sessions.csv            — every session, one row each
  • subjects.csv            — subjects and their colours
  • daily_summary.csv       — per logical-day, per-subject totals (respects 03:00)
  • README.txt              — explains the files

CSV is plain and opens cleanly in Numbers/Excel. JSON remains the only format the
app imports/restores from; the CSVs are for reading and external analysis.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from typing import List

JSON_FILENAME = "jobtracker_backup.json"
SESSIONS_FILENAME = "sessions.csv"
SUBJECTS_FILENAME = "subjects.csv"
DAILY_FILENAME = "daily_summary.csv"
README_FILENAME = "README.txt"


def _hms(seconds: int) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _subject_names(export_data: dict) -> dict:
    names = {}
    for subject in export_data.get("subjects", export_data.get("tasks", [])):
        if subject.get("id") is not None:
            names[subject["id"]] = subject.get("name", "")
    return names


def sessions_csv(export_data: dict) -> str:
    """One row per session, with a readable duration and the subject name."""
    names = _subject_names(export_data)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["subject", "start_time", "end_time", "duration_seconds", "duration_hms"])
    rows = sorted(
        export_data.get("sessions", []),
        key=lambda s: (s.get("start_time") or ""),
    )
    for sess in rows:
        writer.writerow([
            names.get(sess.get("task_id", sess.get("subject_id")), ""),
            sess.get("start_time", ""),
            sess.get("end_time", "") or "",
            sess.get("duration_seconds", 0) or 0,
            _hms(sess.get("duration_seconds", 0) or 0),
        ])
    return buf.getvalue()


def subjects_csv(export_data: dict) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["name", "color", "archived", "notes", "created_at"])
    for subject in export_data.get("subjects", export_data.get("tasks", [])):
        writer.writerow([
            subject.get("name", ""),
            subject.get("color", ""),
            "yes" if subject.get("is_archived") else "no",
            (subject.get("notes") or "").replace("\n", " ").strip(),
            subject.get("created_at", ""),
        ])
    return buf.getvalue()


def daily_summary_csv(breakdown: List[dict]) -> str:
    """Per logical-day, per-subject totals.

    ``breakdown`` is the service's grouped breakdown (already logical-day aware):
    a list of ``{"date", "total_seconds", "segments": [{"subject_name", "seconds"}]}``.
    Segments for the same subject within a day are summed.
    """
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["date", "subject", "hours", "seconds"])
    for day in breakdown:
        per_subject: dict = {}
        for seg in day.get("segments", []):
            name = seg.get("subject_name", "")
            per_subject[name] = per_subject.get(name, 0) + int(seg.get("seconds", 0))
        for name in sorted(per_subject):
            seconds = per_subject[name]
            writer.writerow([day.get("date", ""), name, f"{seconds / 3600:.2f}", seconds])
    return buf.getvalue()


def readme_text() -> str:
    return (
        "JobTracker export bundle\n"
        "========================\n\n"
        f"{JSON_FILENAME}\n"
        "    Full backup in JSON. This is the ONLY file the app can import/restore\n"
        "    from (Settings > Import Backup). Keep it safe.\n\n"
        f"{SESSIONS_FILENAME}\n"
        "    Every tracked session, one row each. Opens in Numbers/Excel.\n\n"
        f"{SUBJECTS_FILENAME}\n"
        "    Your subjects and their colours.\n\n"
        f"{DAILY_FILENAME}\n"
        "    Per-day, per-subject totals. Days use your logical day-start setting\n"
        "    (default 03:00), so late-night work counts on the day it started.\n"
    )


def build_bundle_files(export_data: dict, breakdown: List[dict]) -> dict:
    """Return a mapping of filename -> file contents for the whole bundle."""
    return {
        JSON_FILENAME: json.dumps(export_data, indent=2),
        SESSIONS_FILENAME: sessions_csv(export_data),
        SUBJECTS_FILENAME: subjects_csv(export_data),
        DAILY_FILENAME: daily_summary_csv(breakdown),
        README_FILENAME: readme_text(),
    }


def write_zip(zip_path, export_data: dict, breakdown: List[dict]) -> None:
    """Write the full bundle to a .zip at ``zip_path``."""
    files = build_bundle_files(export_data, breakdown)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
