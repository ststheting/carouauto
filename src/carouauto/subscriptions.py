from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    chat_id INTEGER PRIMARY KEY,
    registered_at TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    revoked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS user_searches (
    search_id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL REFERENCES users(chat_id),
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    min_price REAL,
    max_price REAL,
    exclude_keywords TEXT,
    condition_filter TEXT,
    paused INTEGER NOT NULL DEFAULT 0,
    hide_bumped INTEGER NOT NULL DEFAULT 0,
    max_age_days REAL,
    created_at TEXT NOT NULL,
    UNIQUE(chat_id, name)
);
"""

# Columns added after the table's initial release — see
# _migrate_missing_columns. Each is nullable or has a DEFAULT, so adding it
# in place never requires backfilling existing rows.
_ADDITIVE_COLUMNS = {
    "hide_bumped": "INTEGER NOT NULL DEFAULT 0",
    "max_age_days": "REAL",
}

_SEARCH_COLUMNS = (
    "search_id, chat_id, name, url, min_price, max_price, "
    "exclude_keywords, condition_filter, paused, hide_bumped, max_age_days"
)


@dataclass(frozen=True)
class UserSearch:
    search_id: int
    chat_id: int
    name: str
    url: str
    min_price: float | None
    max_price: float | None
    exclude_keywords: str | None
    condition_filter: str | None
    paused: bool
    hide_bumped: bool
    max_age_days: float | None


def _row_to_user_search(row) -> UserSearch:
    return UserSearch(
        search_id=row[0],
        chat_id=row[1],
        name=row[2],
        url=row[3],
        min_price=row[4],
        max_price=row[5],
        exclude_keywords=row[6],
        condition_filter=row[7],
        paused=bool(row[8]),
        hide_bumped=bool(row[9]),
        max_age_days=row[10],
    )


class SubscriptionStore:
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path)
        self._conn.executescript(_SCHEMA)
        self._migrate_missing_columns()
        self._conn.commit()

    def _migrate_missing_columns(self) -> None:
        # CREATE TABLE IF NOT EXISTS doesn't add columns to a table that
        # already exists from before a field was introduced — add any
        # missing ones in place so existing users' tracked searches survive
        # a deploy, instead of wiping the database.
        existing = {row[1] for row in self._conn.execute("PRAGMA table_info(user_searches)")}
        for column, ddl in _ADDITIVE_COLUMNS.items():
            if column not in existing:
                self._conn.execute(f"ALTER TABLE user_searches ADD COLUMN {column} {ddl}")

    def close(self) -> None:
        self._conn.close()

    def seed_admin(self, chat_id: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """
            INSERT INTO users (chat_id, registered_at, is_admin, revoked)
            VALUES (?, ?, 1, 0)
            ON CONFLICT(chat_id) DO UPDATE SET is_admin = 1, revoked = 0
            """,
            (chat_id, now),
        )
        self._conn.commit()

    def register(self, chat_id: int) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        existing = self._conn.execute(
            "SELECT revoked FROM users WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        if existing is None:
            self._conn.execute(
                "INSERT INTO users (chat_id, registered_at, is_admin, revoked) VALUES (?, ?, 0, 0)",
                (chat_id, now),
            )
            self._conn.commit()
            return True
        if existing[0]:
            self._conn.execute("UPDATE users SET revoked = 0 WHERE chat_id = ?", (chat_id,))
            self._conn.commit()
            return True
        return False

    def is_active(self, chat_id: int) -> bool:
        row = self._conn.execute(
            "SELECT revoked FROM users WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        return row is not None and row[0] == 0

    def is_admin(self, chat_id: int) -> bool:
        row = self._conn.execute(
            "SELECT is_admin, revoked FROM users WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        return row is not None and row[0] == 1 and row[1] == 0

    def revoke(self, chat_id: int) -> bool:
        cur = self._conn.execute("UPDATE users SET revoked = 1 WHERE chat_id = ?", (chat_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def add_search(
        self,
        chat_id: int,
        name: str,
        url: str,
        min_price: float | None = None,
        max_price: float | None = None,
    ) -> int | None:
        now = datetime.now(timezone.utc).isoformat()
        try:
            cur = self._conn.execute(
                """
                INSERT INTO user_searches (chat_id, name, url, min_price, max_price, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (chat_id, name, url, min_price, max_price, now),
            )
            self._conn.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None

    def remove_search(self, chat_id: int, name: str) -> int | None:
        row = self._conn.execute(
            "SELECT search_id FROM user_searches WHERE chat_id = ? AND name = ?",
            (chat_id, name),
        ).fetchone()
        if row is None:
            return None
        self._conn.execute("DELETE FROM user_searches WHERE search_id = ?", (row[0],))
        self._conn.commit()
        return row[0]

    def get_search(self, chat_id: int, name: str) -> UserSearch | None:
        row = self._conn.execute(
            f"SELECT {_SEARCH_COLUMNS} FROM user_searches WHERE chat_id = ? AND name = ?",
            (chat_id, name),
        ).fetchone()
        return _row_to_user_search(row) if row else None

    def list_searches(self, chat_id: int) -> list[UserSearch]:
        rows = self._conn.execute(
            f"SELECT {_SEARCH_COLUMNS} FROM user_searches WHERE chat_id = ? ORDER BY created_at",
            (chat_id,),
        ).fetchall()
        return [_row_to_user_search(r) for r in rows]

    def set_price_filter(
        self, chat_id: int, name: str, min_price: float | None, max_price: float | None
    ) -> bool:
        cur = self._conn.execute(
            "UPDATE user_searches SET min_price = ?, max_price = ? WHERE chat_id = ? AND name = ?",
            (min_price, max_price, chat_id, name),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def set_exclude_keywords(self, chat_id: int, name: str, exclude_keywords: str | None) -> bool:
        cur = self._conn.execute(
            "UPDATE user_searches SET exclude_keywords = ? WHERE chat_id = ? AND name = ?",
            (exclude_keywords, chat_id, name),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def set_condition_filter(self, chat_id: int, name: str, condition_filter: str | None) -> bool:
        cur = self._conn.execute(
            "UPDATE user_searches SET condition_filter = ? WHERE chat_id = ? AND name = ?",
            (condition_filter, chat_id, name),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def set_paused(self, chat_id: int, name: str, paused: bool) -> bool:
        cur = self._conn.execute(
            "UPDATE user_searches SET paused = ? WHERE chat_id = ? AND name = ?",
            (1 if paused else 0, chat_id, name),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def set_hide_bumped(self, chat_id: int, name: str, hide_bumped: bool) -> bool:
        cur = self._conn.execute(
            "UPDATE user_searches SET hide_bumped = ? WHERE chat_id = ? AND name = ?",
            (1 if hide_bumped else 0, chat_id, name),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def set_max_age(self, chat_id: int, name: str, max_age_days: float | None) -> bool:
        cur = self._conn.execute(
            "UPDATE user_searches SET max_age_days = ? WHERE chat_id = ? AND name = ?",
            (max_age_days, chat_id, name),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def list_active_subscriptions(self) -> list[UserSearch]:
        rows = self._conn.execute(
            """
            SELECT us.search_id, us.chat_id, us.name, us.url, us.min_price, us.max_price,
                   us.exclude_keywords, us.condition_filter, us.paused, us.hide_bumped,
                   us.max_age_days
            FROM user_searches us
            JOIN users u ON u.chat_id = us.chat_id
            WHERE us.paused = 0 AND u.revoked = 0
            """
        ).fetchall()
        return [_row_to_user_search(r) for r in rows]
