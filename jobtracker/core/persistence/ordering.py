"""Manual ordering helpers on the shared connection."""

from __future__ import annotations



class OrderingRepository:
    """Manual ordering helpers on the shared connection."""

    def __init__(self, database):
        self._database = database

    def _next_sort_order(self, table: str) -> int:
        cur = self._database.connection.cursor()
        cur.execute(f"SELECT COALESCE(MAX(sort_order), 0) + 1 AS next_order FROM {table}")
        return int(cur.fetchone()["next_order"])

    def _set_order(self, table: str, id_column: str, ordered_ids: list[int]) -> None:
        normalized_ids: list[int] = []
        seen: set[int] = set()
        for raw_id in ordered_ids:
            try:
                row_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            if row_id in seen:
                continue
            seen.add(row_id)
            normalized_ids.append(row_id)

        if not normalized_ids:
            return

        cur = self._database.connection.cursor()
        cur.execute(f"SELECT {id_column} FROM {table} ORDER BY sort_order ASC, {id_column} ASC")
        existing_ids = [int(row[id_column]) for row in cur.fetchall()]
        if not existing_ids:
            return

        ordered_existing = [row_id for row_id in normalized_ids if row_id in existing_ids]
        missing_existing = [row_id for row_id in existing_ids if row_id not in ordered_existing]
        final_ids = ordered_existing + missing_existing

        for idx, row_id in enumerate(final_ids, start=1):
            cur.execute(
                f"UPDATE {table} SET sort_order = ? WHERE {id_column} = ?",
                (idx, row_id),
            )
        self._database.connection.commit()

    def _set_filtered_order(
        self,
        table: str,
        id_column: str,
        ordered_ids: list[int],
        where_clause: str,
        where_params: tuple,
    ) -> None:
        """Persist an order inside one filtered view without disturbing peers."""
        normalized_ids: list[int] = []
        seen: set[int] = set()
        for raw_id in ordered_ids:
            try:
                row_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            if row_id not in seen:
                seen.add(row_id)
                normalized_ids.append(row_id)
        if not normalized_ids:
            return

        cur = self._database.connection.cursor()
        cur.execute(
            f"SELECT {id_column} FROM {table} WHERE {where_clause} "
            f"ORDER BY sort_order ASC, {id_column} ASC",
            where_params,
        )
        existing_ids = [int(row[id_column]) for row in cur.fetchall()]
        final_ids = (
            [row_id for row_id in normalized_ids if row_id in existing_ids]
            + [row_id for row_id in existing_ids if row_id not in normalized_ids]
        )
        for idx, row_id in enumerate(final_ids, start=1):
            cur.execute(
                f"UPDATE {table} SET sort_order = ? "
                f"WHERE {id_column} = ? AND {where_clause}",
                (idx, row_id, *where_params),
            )
        self._database.connection.commit()
