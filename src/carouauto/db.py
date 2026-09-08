from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_listings (
    search_name TEXT NOT NULL,
    listing_id TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY (search_name, listing_id)
);
"""


class SeenStore:
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def diff_and_update(self, search_name: str, listing_ids: list[str]) -> list[str]:
        now = datetime.now(timezone.utc).isoformat()

        row = self._conn.execute(
            "SELECT COUNT(*) FROM seen_listings WHERE search_name = ?",
            (search_name,),
        ).fetchone()
        is_first_run = row[0] == 0

        new_ids: list[str] = []
        if not is_first_run and listing_ids:
            placeholders = ",".join("?" for _ in listing_ids)
            rows = self._conn.execute(
                f"SELECT listing_id FROM seen_listings "
                f"WHERE search_name = ? AND listing_id IN ({placeholders})",
                (search_name, *listing_ids),
            )
            existing_ids = {r[0] for r in rows}
            new_ids = [lid for lid in listing_ids if lid not in existing_ids]

        self._conn.executemany(
            """
            INSERT INTO seen_listings (search_name, listing_id, first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(search_name, listing_id)
            DO UPDATE SET last_seen_at = excluded.last_seen_at
            """,
            [(search_name, lid, now, now) for lid in listing_ids],
        )
        self._conn.commit()
        return new_ids
