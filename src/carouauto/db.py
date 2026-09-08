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

    def get_new_ids(self, search_name: str, listing_ids: list[str]) -> list[str]:
        """Return the listing ids not yet seen for this search, without recording anything.

        On a search's first-ever call this returns [] (silent seeding), matching
        diff_and_update's semantics. This method never writes, so a caller may
        safely abort (e.g. a failed notify) and see the same ids again next time.
        """
        row = self._conn.execute(
            "SELECT COUNT(*) FROM seen_listings WHERE search_name = ?",
            (search_name,),
        ).fetchone()
        is_first_run = row[0] == 0

        if is_first_run or not listing_ids:
            return []

        placeholders = ",".join("?" for _ in listing_ids)
        rows = self._conn.execute(
            f"SELECT listing_id FROM seen_listings "
            f"WHERE search_name = ? AND listing_id IN ({placeholders})",
            (search_name, *listing_ids),
        )
        existing_ids = {r[0] for r in rows}
        return [lid for lid in listing_ids if lid not in existing_ids]

    def mark_seen(self, search_name: str, listing_ids: list[str]) -> None:
        """Record every id as seen for this search (insert new, refresh last_seen_at)."""
        now = datetime.now(timezone.utc).isoformat()
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

    def diff_and_update(self, search_name: str, listing_ids: list[str]) -> list[str]:
        """Convenience wrapper: compute new ids and immediately mark them all seen.

        Prefer get_new_ids + mark_seen when the caller does fallible work (a
        Telegram send) between the two, so a failure doesn't lose the ids.
        """
        new_ids = self.get_new_ids(search_name, listing_ids)
        self.mark_seen(search_name, listing_ids)
        return new_ids
