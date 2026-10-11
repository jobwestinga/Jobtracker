"""Mirror integrity reports: row counts, stable UID digests and local clocks.

Report fetching and repair decisions stay in the engine. Verification accepts
its hash/clock callbacks so SyncEngine overrides continue to govern a pass.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime

from ..core import sync_policy, timeutils

logger = logging.getLogger("jobtracker")


def verify_report(database, report: dict, result, *, check_clock, uid_hash) -> bool:
    """Compare the mirror with the server. True when they agree.

    Counts catch anything added or lost and are cheap enough to check every
    pass. When the report includes UID hashes, compare those too to catch the
    rarer case of the right *number* of the wrong rows.
    """
    check_clock(report, result)
    cur = database.connection.cursor()
    for api_name, expected in report.get("tables", {}).items():
        table = sync_policy.TABLE_FOR_API_NAME.get(api_name)
        if table is None:
            continue
        cur.execute(f"SELECT COUNT(*) AS n FROM {table}")
        local = int(cur.fetchone()["n"])
        if local != int(expected.get("count", -1)):
            result.messages.append(
                f"{api_name}: mirror has {local}, server has {expected.get('count')}"
            )
            return False
        wanted_hash = expected.get("hash")
        if wanted_hash and uid_hash(table) != wanted_hash:
            result.messages.append(f"{api_name}: same row count, different rows")
            return False
    return True


def hash_uids(database, table: str) -> str:
    """The same digest the server computes, over this mirror's uids."""
    cur = database.connection.cursor()
    cur.execute(f"SELECT uid FROM {table} ORDER BY uid")
    uids = [row["uid"] or "" for row in cur.fetchall()]
    return hashlib.sha256("\n".join(uids).encode("utf-8")).hexdigest()


def check_clock(report: dict, result, *, clock=None) -> None:
    """Complain if the server's wall clock is not ours.

    JobTracker stores naive local times. If the server sits in another
    timezone, a session started from the phone is filed hours off and nothing
    else would ever notice — the rows look perfectly valid.
    """
    stamp = report.get("server_time")
    if not stamp:
        return
    server_now = timeutils.parse_iso(stamp)
    if server_now is None:
        return
    skew = abs(((clock or datetime.now)() - server_now).total_seconds())
    if skew > 300:
        message = (
            f"server clock is {round(skew / 60)} min from this machine's; "
            "sessions will be recorded at the wrong time"
        )
        logger.warning("Sync: %s", message)
        result.messages.append(message)
