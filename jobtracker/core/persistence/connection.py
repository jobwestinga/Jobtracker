"""One SQLite connection and nested transaction/commit ownership."""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager

logger = logging.getLogger("jobtracker")


class _AtomicConnection(sqlite3.Connection):
    """Keep existing CRUD commits inside a caller's larger transaction."""

    _atomic_depth = 0

    def commit(self) -> None:
        if not self._atomic_depth:
            super().commit()


def open_connection(path: str) -> _AtomicConnection:
    connection = sqlite3.connect(
        path, check_same_thread=False, factory=_AtomicConnection
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    # WAL lets the sync worker use its own connection while the UI reads.
    # The timeout handles the brief collisions without blocking normal reads.
    try:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 5000")
    except sqlite3.Error:
        logger.warning("Could not enable WAL journal mode", exc_info=True)
    return connection


@contextmanager
def atomic(connection: _AtomicConnection):
    """Defer method commits and use savepoints inside an existing transaction."""
    owns_transaction = not connection.in_transaction
    savepoint = f"jobtracker_atomic_{connection._atomic_depth}"
    connection.execute("BEGIN IMMEDIATE" if owns_transaction else f"SAVEPOINT {savepoint}")
    connection._atomic_depth += 1
    try:
        yield
    except BaseException:
        connection._atomic_depth -= 1
        if owns_transaction:
            sqlite3.Connection.rollback(connection)
        else:
            connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            connection.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise
    else:
        connection._atomic_depth -= 1
        if owns_transaction:
            try:
                sqlite3.Connection.commit(connection)
            except BaseException:
                sqlite3.Connection.rollback(connection)
                raise
        else:
            connection.execute(f"RELEASE SAVEPOINT {savepoint}")
