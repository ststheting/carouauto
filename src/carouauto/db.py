from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_listings (
    search_id INTEGER NOT NULL,
    listing_id TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY (search_id, listing_id)
);
"""


class SeenStore:
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def get_new_ids(self, search_id: int, listing_ids: list[str]) -> list[str]:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM seen_listings WHERE search_id = ?",
            (search_id,),
        ).fetchone()
        is_first_run = row[0] == 0

        if is_first_run or not listing_ids:
            return []

        placeholders = ",".join("?" for _ in listing_ids)
        rows = self._conn.execute(
            f"SELECT listing_id FROM seen_listings "
            f"WHERE search_id = ? AND listing_id IN ({placeholders})",
            (search_id, *listing_ids),
        )
        existing_ids = {r[0] for r in rows}
        return [lid for lid in listing_ids if lid not in existing_ids]

    def mark_seen(self, search_id: int, listing_ids: list[str]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.executemany(
            """
            INSERT INTO seen_listings (search_id, listing_id, first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(search_id, listing_id)
            DO UPDATE SET last_seen_at = excluded.last_seen_at
            """,
            [(search_id, lid, now, now) for lid in listing_ids],
        )
        self._conn.commit()

    def diff_and_update(self, search_id: int, listing_ids: list[str]) -> list[str]:
        new_ids = self.get_new_ids(search_id, listing_ids)
        self.mark_seen(search_id, listing_ids)
        return new_ids

    def delete_all_for_search(self, search_id: int) -> None:
        self._conn.execute("DELETE FROM seen_listings WHERE search_id = ?", (search_id,))
        self._conn.commit()
