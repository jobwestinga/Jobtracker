"""One desktop engine per store, for the lifetime of the owning process.

The lock is an ``flock`` on a small file keyed by the canonical database path.
The kernel releases it when the owner exits or crashes, so a leftover file never
counts as a live owner. Lock files live outside the store's folder (and outside
iCloud) so they never travel in a backup or a synced checkout.

Both the native engine and ``main.py`` acquire this before constructing services
or running recovery. Only this module's own imports are used, so it is safe to
call before the database singleton exists.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path


class StoreInUse(RuntimeError):
    """Another live process already owns this store."""

    def __init__(self, path: Path, owner: dict):
        self.path = path
        self.owner = owner
        super().__init__(f"{path} is already open in another JobTracker process")


def lock_dir() -> Path:
    override = os.environ.get("JOBTRACKER_LOCK_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / "Library" / "Caches" / "JobTracker" / "locks"


def lock_path_for(db_path: Path) -> Path:
    canonical = os.path.realpath(Path(db_path).expanduser())
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
    return lock_dir() / f"store-{digest}.lock"


class StoreLock:
    """Hold an exclusive, non-blocking ``flock`` until :meth:`release`."""

    def __init__(self, db_path: Path, *, role: str):
        self.db_path = Path(os.path.realpath(Path(db_path).expanduser()))
        self.role = role
        self.path = lock_path_for(self.db_path)
        self._fd: int | None = None

    def acquire(self) -> "StoreLock":
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            owner = _read_owner(fd)
            os.close(fd)
            if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
                raise StoreInUse(self.db_path, owner) from None
            raise
        os.ftruncate(fd, 0)
        os.write(fd, json.dumps({"pid": os.getpid(), "role": self.role, "db": str(self.db_path)}).encode())
        self._fd = fd
        return self

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *_exc):
        self.release()


def _read_owner(fd: int) -> dict:
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        return json.loads(os.read(fd, 4096).decode() or "{}")
    except (OSError, ValueError):
        return {}
