"""Load the server's first copy of the data from a desktop backup.

Run once, when the server database is still empty:

    python -m server.seed /path/to/jobtracker_backup.json

Uses the app's own ``import_data()``, so the file is exactly what
**Settings → Export Backup** produces — no separate format to keep in step. Row
uids in the backup are preserved, so the desktop and the server agree about which
row is which from the very first sync.

Refuses to run against a database that already holds data unless ``--force`` is
given: seeding twice is not the same as syncing twice, and the heuristic matching
inside ``import_data`` is for restores, not for merging live machines.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import zipfile
from pathlib import Path

from jobtracker.core import sync_policy
from jobtracker.core.config import DB_PATH
from jobtracker.core.database import Database

logger = logging.getLogger("jobtracker.server")


def load_payload(path: Path) -> dict:
    """Accept either the .json backup or the .zip bundle around it."""
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as bundle:
            name = next(
                (n for n in bundle.namelist() if n.endswith(".json")), None
            )
            if name is None:
                raise SystemExit(f"{path} contains no .json backup")
            return json.loads(bundle.read(name))
    return json.loads(path.read_text())


def row_counts(database: Database) -> dict[str, int]:
    cur = database.connection.cursor()
    counts = {}
    for table in sync_policy.SYNCED_TABLES:
        cur.execute(f"SELECT COUNT(*) AS n FROM {table}")
        counts[table] = int(cur.fetchone()["n"])
    return counts


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup", type=Path, help="jobtracker_backup.json or .zip")
    parser.add_argument("--db", default=os.environ.get("JOBTRACKER_DB_PATH") or str(DB_PATH))
    parser.add_argument(
        "--force", action="store_true", help="seed even if the database has rows"
    )
    args = parser.parse_args(argv)

    if not args.backup.exists():
        raise SystemExit(f"no such backup: {args.backup}")

    database = Database(args.db)
    before = row_counts(database)
    if any(before.values()) and not args.force:
        print("Server database already has data:", before, file=sys.stderr)
        print("Refusing to seed twice. Use --force only if you mean it.", file=sys.stderr)
        return 1

    payload = load_payload(args.backup)
    database.import_data(payload)
    after = row_counts(database)

    print(f"Seeded {args.db}")
    for table, count in after.items():
        print(f"  {table:16} {before[table]:6} -> {count}")
    database.connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
