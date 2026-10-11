"""Wire/local identity mapping without changing UID triggers."""

from __future__ import annotations

import logging
from typing import Optional

from .. import sync_policy

logger = logging.getLogger("jobtracker")


class IdentityRepository:
    """Wire/local identity mapping without changing UID triggers."""

    def __init__(self, database):
        self._database = database

    def id_for_uid(self, table: str, uid: str) -> Optional[int]:
        """Translate a wire identity into this database's local row id.

        Returns None when this database has never seen the uid — the caller
        decides whether that is a 404 or a row to create.
        """
        if table not in sync_policy.SYNCED_TABLES:
            raise ValueError(f"{table} is not a synced table")
        if not isinstance(uid, str) or not uid.strip():
            return None
        cur = self._database.connection.cursor()
        cur.execute(f"SELECT id FROM {table} WHERE uid = ?", (uid.strip(),))
        row = cur.fetchone()
        return int(row["id"]) if row else None

    def uid_for_id(self, table: str, row_id: int) -> Optional[str]:
        """Translate a local row id into the identity other machines know."""
        if table not in sync_policy.SYNCED_TABLES:
            raise ValueError(f"{table} is not a synced table")
        if row_id is None:
            return None
        cur = self._database.connection.cursor()
        cur.execute(f"SELECT uid FROM {table} WHERE id = ?", (int(row_id),))
        row = cur.fetchone()
        return row["uid"] if row else None

    def _importable_uid(self, table: str, value) -> Optional[str]:
        """The uid to give an imported row, or None to let the trigger mint one.

        Backups carry uids so that restoring one keeps each row's cross-machine
        identity instead of silently forking it. A uid already used in this table
        is dropped rather than allowed to violate the unique index — the row
        still imports, it just gets a fresh identity.
        """
        if not isinstance(value, str) or not value.strip():
            return None
        uid = value.strip()
        cur = self._database.connection.cursor()
        cur.execute(f"SELECT 1 FROM {table} WHERE uid = ?", (uid,))
        if cur.fetchone():
            logger.warning(
                "Import: uid %s already present in %s; assigning a new one", uid, table
            )
            return None
        return uid
