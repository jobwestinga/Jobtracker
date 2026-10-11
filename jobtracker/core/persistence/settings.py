"""Device preferences and device identity persistence."""

from __future__ import annotations

import logging

from .. import sync_policy

logger = logging.getLogger("jobtracker")


class SettingsRepository:
    """Device preferences and device identity persistence."""

    def __init__(self, database):
        self._database = database

    def get_setting(self, key: str, default: str = "") -> str:
        cur = self._database.connection.cursor()
        cur.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cur.fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        cur = self._database.connection.cursor()
        cur.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self._database.connection.commit()

    def delete_setting(self, key: str) -> None:
        self._database.connection.execute("DELETE FROM settings WHERE key = ?", (key,))
        self._database.connection.commit()

    def get_or_create_device_id(self) -> str:
        """This install's stable identity, minted on first use.

        Used to decide who owns a running session: only the device that started
        one may auto-close it during crash recovery. Deliberately stored in
        ``settings`` and excluded from backups' restore path, so copying a
        database or a backup to another machine does not clone its identity.
        """
        existing = self._database.get_setting(sync_policy.DEVICE_ID_SETTING, "")
        if existing.strip():
            return existing.strip()
        device_id = sync_policy.new_uid()
        self._database.set_setting(sync_policy.DEVICE_ID_SETTING, device_id)
        logger.info("Assigned this install device_id %s", device_id)
        return device_id
