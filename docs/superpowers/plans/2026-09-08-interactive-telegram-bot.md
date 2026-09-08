# Interactive Multi-User Telegram Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn carouauto from a one-way, single-recipient pusher with a
static `config.yaml` search list into an interactive, multi-user Telegram
bot: password-gated self-registration, per-user private search lists with
efficient URL-deduplicated shared polling, price/keyword/condition
filters, pause/resume, admin revoke, and on-demand backup.

**Architecture:** Two concurrent asyncio tasks in one process — the
existing Carousell poll loop (rewritten to fetch each distinct URL once
and fan out to every subscriber of that URL) and a new Telegram
long-polling command listener. Both share one `SeenStore` (now keyed by
`search_id`) and a new `SubscriptionStore` (users + their searches),
both backed by the same SQLite file via independent connections.

**Tech Stack:** Same as the existing project (Python 3.11+, Playwright,
BeautifulSoup4, httpx, PyYAML, python-dotenv, pytest + pytest-asyncio) —
no new dependencies.

**Spec:** [docs/superpowers/specs/2026-09-08-interactive-telegram-bot-design.md](../specs/2026-09-08-interactive-telegram-bot-design.md)

## Global Constraints

- Every Telegram `chat_id` is handled as `int` throughout (Telegram's API
  returns it as a JSON number; the DB schema declares it `INTEGER`).
- Command handlers are `async def`, take `(args: list[str], chat_id: int,
  ctx: BotContext)`, and return `str` — an empty string means "send no
  reply" (used only by `/backup`, which sends a document itself first).
  A handler must never raise; the dispatcher wraps every call so one
  broken command can never crash the listener loop.
- `SeenStore` and `SubscriptionStore` each open their own
  `sqlite3.connect(db_path)` to the same file — they stay fully
  decoupled classes, consistent with the project's one-class-per-concern
  pattern.
- Operational alerts (a Cloudflare challenge on a URL, the dead-browser
  restart) go to the seeded **admin** `chat_id` only — regular users have
  no VPS/SSH access and can't act on them; per-listing notifications go
  to the specific subscriber whose search matched.
- The poll loop re-reads the current subscription list **every round**
  (`SubscriptionStore.list_active_subscriptions`, called fresh, not
  captured once at startup) so `/add`/`/remove`/`/pause` take effect
  without a restart.
- A poll round with **zero** total subscriptions is never counted as a
  failure for the dead-browser-detection counter — there's simply
  nothing to poll yet, which is not the same as everything failing.
- `poll_interval_seconds` defaults to `300` (5 minutes) and is not
  bot-configurable in this plan; `/help` and `/status` must state it so
  users don't expect near-instant delivery.
- Every module with non-trivial logic gets real fixture/fake-based unit
  tests, following the project's existing style (real sqlite via
  `tmp_path`, fake notifiers/fetchers, no mocking libraries). The
  Telegram long-polling I/O itself is verified manually, consistent with
  how the Playwright poller is already handled — not mocked.
- This is a clean cutover of the SQLite schema (per the spec's migration
  note) — no migration-in-place logic for the old single-tenant schema.

---

## Task 1: Extend `Listing` and the parser to capture `condition`

**Files:**
- Modify: `src/carouauto/models.py`
- Modify: `src/carouauto/parser.py`
- Modify: `tests/fixtures/search_results_normal.html`
- Modify: `tests/test_parser.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `Listing.condition: str` (e.g. `"Well used"`, `"Brand new"`),
  consumed by `filters.py` (Task 4) and the scheduler/command handlers.

The condition text is already present in every fixture and every real
page seen so far, as the `<p>` sibling immediately following the div that
wraps the price element — e.g. in the current fixture,
`<div><p title="S$599">S$599</p></div><p>Well used</p>`. Locate it via
`price_el.parent.find_next_sibling("p")`, not a CSS class (classes are
hashed/build-specific on the real site, per the existing parser's
established rule).

- [ ] **Step 1: Add the field to `Listing`**

In `src/carouauto/models.py`, add `condition: str` as the last field:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class Listing:
    listing_id: str
    title: str
    price: str
    url: str
    thumbnail_url: str
    posted_text: str
    condition: str
```

- [ ] **Step 2: Update the fixture to make the condition text unambiguous for both cards**

`tests/fixtures/search_results_normal.html` already has `Well used` and
`Brand new` as the condition text for its two cards — no change needed
to the fixture file itself.

- [ ] **Step 3: Write the failing test**

Add to `tests/test_parser.py` (the existing
`test_extracts_correct_fields_for_a_listing` now needs `condition=` added
to its expected `Listing(...)` — update it in place, and add one new
test):

```python
def test_extracts_correct_fields_for_a_listing():
    html = (FIXTURES / "search_results_normal.html").read_text()

    listings = parse_listings(html)
    speediance = next(l for l in listings if l.listing_id == "1460198499")

    assert speediance == Listing(
        listing_id="1460198499",
        title="Speediance Gym Monster Smart Home Fitness Machine",
        price="S$599",
        url="https://www.carousell.sg/p/speediance-gym-monster-smart-home-fitness-machine-1460198499/",
        thumbnail_url="https://media.karousell.com/media/photos/products/speediance_thumb.jpg",
        posted_text="18 hours ago",
        condition="Well used",
    )


def test_extracts_condition_for_second_listing():
    html = (FIXTURES / "search_results_normal.html").read_text()

    listings = parse_listings(html)
    switch = next(l for l in listings if l.listing_id == "1451610227")

    assert switch.condition == "Brand new"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_parser.py -v`
Expected: FAIL — `TypeError: Listing.__init__() missing 1 required
positional argument: 'condition'` (or a mismatch on the new assertion).

- [ ] **Step 3: Implement the parser change**

In `src/carouauto/parser.py`, after the existing `price_el`/`price` block
and before constructing the `Listing`, add:

```python
        condition = ""
        if price_el is not None:
            price_wrapper = price_el.parent
            condition_el = price_wrapper.find_next_sibling("p") if price_wrapper else None
            if condition_el is not None:
                condition = condition_el.get_text(strip=True)
```

And add `condition=condition,` to the `Listing(...)` construction.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_parser.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/carouauto/models.py src/carouauto/parser.py tests/test_parser.py
git commit -m "feat: capture listing condition (Well used / Brand new / etc.)"
```

---

## Task 2: `SubscriptionStore` — users and their tracked searches

**Files:**
- Create: `src/carouauto/subscriptions.py`
- Create: `tests/test_subscriptions.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `carouauto.subscriptions.UserSearch` (frozen dataclass:
  `search_id: int`, `chat_id: int`, `name: str`, `url: str`,
  `min_price: float | None`, `max_price: float | None`,
  `exclude_keywords: str | None`, `condition_filter: str | None`,
  `paused: bool`) and `carouauto.subscriptions.SubscriptionStore` with:
  - `__init__(self, db_path: str)`, `close(self) -> None`
  - `seed_admin(self, chat_id: int) -> None`
  - `register(self, chat_id: int) -> bool`
  - `is_active(self, chat_id: int) -> bool`
  - `is_admin(self, chat_id: int) -> bool`
  - `revoke(self, chat_id: int) -> bool`
  - `add_search(self, chat_id: int, name: str, url: str, min_price: float | None = None, max_price: float | None = None) -> int | None`
  - `remove_search(self, chat_id: int, name: str) -> int | None`
  - `get_search(self, chat_id: int, name: str) -> UserSearch | None`
  - `list_searches(self, chat_id: int) -> list[UserSearch]`
  - `set_price_filter(self, chat_id: int, name: str, min_price: float | None, max_price: float | None) -> bool`
  - `set_exclude_keywords(self, chat_id: int, name: str, exclude_keywords: str | None) -> bool`
  - `set_condition_filter(self, chat_id: int, name: str, condition_filter: str | None) -> bool`
  - `set_paused(self, chat_id: int, name: str, paused: bool) -> bool`
  - `list_active_subscriptions(self) -> list[UserSearch]` (non-paused
    searches belonging to non-revoked users — this is what the poller
    consumes in Task 11)

This is used by every command handler (Tasks 6-9) and the scheduler
(Task 11).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_subscriptions.py`:

```python
from carouauto.subscriptions import SubscriptionStore


def test_seed_admin_makes_a_new_chat_id_an_admin(tmp_path):
    store = SubscriptionStore(str(tmp_path / "t.sqlite3"))

    store.seed_admin(111)

    assert store.is_admin(111) is True
    assert store.is_active(111) is True


def test_seed_admin_is_idempotent(tmp_path):
    store = SubscriptionStore(str(tmp_path / "t.sqlite3"))

    store.seed_admin(111)
    store.seed_admin(111)

    assert store.is_admin(111) is True


def test_register_new_user_succeeds_once(tmp_path):
    store = SubscriptionStore(str(tmp_path / "t.sqlite3"))

    assert store.register(222) is True
    assert store.is_active(222) is True
    assert store.is_admin(222) is False
    assert store.register(222) is False  # already registered


def test_unregistered_user_is_not_active(tmp_path):
    store = SubscriptionStore(str(tmp_path / "t.sqlite3"))

    assert store.is_active(999) is False
    assert store.is_admin(999) is False


def test_revoke_deactivates_a_user_but_allows_re_registration(tmp_path):
    store = SubscriptionStore(str(tmp_path / "t.sqlite3"))
    store.register(222)

    assert store.revoke(222) is True
    assert store.is_active(222) is False

    assert store.revoke(999) is False  # never existed

    assert store.register(222) is True  # re-registering un-revokes
    assert store.is_active(222) is True


def test_add_search_then_get_and_list(tmp_path):
    store = SubscriptionStore(str(tmp_path / "t.sqlite3"))
    store.register(222)

    search_id = store.add_search(222, "speediance", "https://example.com/s", 100.0, 500.0)

    assert search_id is not None
    found = store.get_search(222, "speediance")
    assert found.search_id == search_id
    assert found.chat_id == 222
    assert found.name == "speediance"
    assert found.url == "https://example.com/s"
    assert found.min_price == 100.0
    assert found.max_price == 500.0
    assert found.exclude_keywords is None
    assert found.condition_filter is None
    assert found.paused is False
    assert store.list_searches(222) == [found]


def test_add_search_rejects_duplicate_name_for_same_user(tmp_path):
    store = SubscriptionStore(str(tmp_path / "t.sqlite3"))
    store.register(222)
    store.add_search(222, "speediance", "https://example.com/s")

    assert store.add_search(222, "speediance", "https://example.com/other") is None


def test_two_users_can_use_the_same_search_name(tmp_path):
    store = SubscriptionStore(str(tmp_path / "t.sqlite3"))
    store.register(222)
    store.register(333)

    id_a = store.add_search(222, "speediance", "https://example.com/s")
    id_b = store.add_search(333, "speediance", "https://example.com/s")

    assert id_a != id_b
    assert store.get_search(222, "speediance").search_id == id_a
    assert store.get_search(333, "speediance").search_id == id_b


def test_remove_search_deletes_it_and_returns_its_id(tmp_path):
    store = SubscriptionStore(str(tmp_path / "t.sqlite3"))
    store.register(222)
    search_id = store.add_search(222, "speediance", "https://example.com/s")

    assert store.remove_search(222, "speediance") == search_id
    assert store.get_search(222, "speediance") is None
    assert store.remove_search(222, "speediance") is None  # already gone


def test_set_price_exclude_condition_and_paused(tmp_path):
    store = SubscriptionStore(str(tmp_path / "t.sqlite3"))
    store.register(222)
    store.add_search(222, "speediance", "https://example.com/s")

    assert store.set_price_filter(222, "speediance", 50.0, 200.0) is True
    assert store.set_exclude_keywords(222, "speediance", "case,box only") is True
    assert store.set_condition_filter(222, "speediance", "Brand new") is True
    assert store.set_paused(222, "speediance", True) is True

    found = store.get_search(222, "speediance")
    assert (found.min_price, found.max_price) == (50.0, 200.0)
    assert found.exclude_keywords == "case,box only"
    assert found.condition_filter == "Brand new"
    assert found.paused is True

    assert store.set_price_filter(222, "does-not-exist", 1.0, 2.0) is False


def test_list_active_subscriptions_excludes_paused_and_revoked(tmp_path):
    store = SubscriptionStore(str(tmp_path / "t.sqlite3"))
    store.register(222)
    store.register(333)
    active_id = store.add_search(222, "active", "https://example.com/a")
    store.add_search(222, "paused", "https://example.com/b")
    store.set_paused(222, "paused", True)
    store.add_search(333, "revoked-users", "https://example.com/c")
    store.revoke(333)

    subs = store.list_active_subscriptions()

    assert [s.search_id for s in subs] == [active_id]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_subscriptions.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'carouauto.subscriptions'`

- [ ] **Step 3: Implement `src/carouauto/subscriptions.py`**

```python
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
    created_at TEXT NOT NULL,
    UNIQUE(chat_id, name)
);
"""

_SEARCH_COLUMNS = (
    "search_id, chat_id, name, url, min_price, max_price, "
    "exclude_keywords, condition_filter, paused"
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
    )


class SubscriptionStore:
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def seed_admin(self, chat_id: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """
            INSERT INTO users (chat_id, registered_at, is_admin, revoked)
            VALUES (?, ?, 1, 0)
            ON CONFLICT(chat_id) DO UPDATE SET is_admin = 1
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

    def list_active_subscriptions(self) -> list[UserSearch]:
        rows = self._conn.execute(
            """
            SELECT us.search_id, us.chat_id, us.name, us.url, us.min_price, us.max_price,
                   us.exclude_keywords, us.condition_filter, us.paused
            FROM user_searches us
            JOIN users u ON u.chat_id = us.chat_id
            WHERE us.paused = 0 AND u.revoked = 0
            """
        ).fetchall()
        return [_row_to_user_search(r) for r in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_subscriptions.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add src/carouauto/subscriptions.py tests/test_subscriptions.py
git commit -m "feat: add SubscriptionStore for multi-user registration and per-user searches"
```

---

## Task 3: Re-key `SeenStore` from `search_name` to `search_id`

**Files:**
- Modify: `src/carouauto/db.py`
- Modify: `tests/test_db.py`

**Interfaces:**
- Consumes: nothing new (this is an internal rekey).
- Produces: `SeenStore.get_new_ids(search_id: int, listing_ids: list[str]) -> list[str]`,
  `SeenStore.mark_seen(search_id: int, listing_ids: list[str]) -> None`,
  `SeenStore.diff_and_update(search_id: int, listing_ids: list[str]) -> list[str]`
  (all now take `search_id: int` instead of `search_name: str`), plus a
  new `SeenStore.delete_all_for_search(search_id: int) -> None` — used by
  the `/remove` command handler (Task 7) to clean up a removed search's
  seen-listing history.

This is a clean cutover per the spec's migration note — the live
deployment's existing `seen_listings` rows (keyed by the old
`search_name` column) are not migrated; `mark_seen`/`get_new_ids` simply
operate on the new `search_id` column from this point on.

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_db.py` in full (every `"speediance"`/`"nintendo-switch"`
string search name becomes an integer `search_id`, and one new test for
`delete_all_for_search` is added):

```python
from carouauto.db import SeenStore


def test_first_run_seeds_silently_and_reports_no_new_ids(tmp_path):
    store = SeenStore(str(tmp_path / "test.sqlite3"))

    new_ids = store.diff_and_update(1, ["111", "222"])

    assert new_ids == []


def test_second_run_reports_only_genuinely_new_ids(tmp_path):
    store = SeenStore(str(tmp_path / "test.sqlite3"))
    store.diff_and_update(1, ["111", "222"])

    new_ids = store.diff_and_update(1, ["111", "222", "333"])

    assert new_ids == ["333"]


def test_searches_are_tracked_independently(tmp_path):
    store = SeenStore(str(tmp_path / "test.sqlite3"))
    store.diff_and_update(1, ["111"])

    new_ids = store.diff_and_update(2, ["111"])

    assert new_ids == []  # first run for THIS search_id, seeded silently


def test_state_persists_across_store_instances(tmp_path):
    db_path = str(tmp_path / "test.sqlite3")
    store_one = SeenStore(db_path)
    store_one.diff_and_update(1, ["111"])
    store_one.close()

    store_two = SeenStore(db_path)
    new_ids = store_two.diff_and_update(1, ["111", "222"])

    assert new_ids == ["222"]


def test_get_new_ids_returns_nothing_on_first_run(tmp_path):
    store = SeenStore(str(tmp_path / "test.sqlite3"))

    assert store.get_new_ids(1, ["111", "222"]) == []


def test_get_new_ids_does_not_mutate_state(tmp_path):
    store = SeenStore(str(tmp_path / "test.sqlite3"))
    store.mark_seen(1, ["111"])

    first = store.get_new_ids(1, ["111", "222", "333"])
    second = store.get_new_ids(1, ["111", "222", "333"])

    assert first == ["222", "333"]
    assert second == first


def test_mark_seen_persists_so_ids_are_no_longer_new(tmp_path):
    store = SeenStore(str(tmp_path / "test.sqlite3"))
    store.mark_seen(1, ["111"])
    assert store.get_new_ids(1, ["111", "222"]) == ["222"]

    store.mark_seen(1, ["111", "222"])

    assert store.get_new_ids(1, ["111", "222"]) == []


def test_mark_seen_ends_the_first_run_for_a_search(tmp_path):
    store = SeenStore(str(tmp_path / "test.sqlite3"))

    store.mark_seen(1, ["111"])

    assert store.get_new_ids(2, ["111"]) == []


def test_delete_all_for_search_removes_its_history(tmp_path):
    store = SeenStore(str(tmp_path / "test.sqlite3"))
    store.mark_seen(1, ["111", "222"])
    store.mark_seen(2, ["111"])

    store.delete_all_for_search(1)

    # search_id 1's history is gone, so its old ids look new again.
    assert store.get_new_ids(1, ["111"]) == []  # first run again, seeded silently
    # search_id 2 is untouched.
    assert store.get_new_ids(2, ["111", "333"]) == ["333"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_db.py -v`
Expected: FAIL — `sqlite3.OperationalError` or type errors, since the
current schema/queries use `search_name` and expect strings.

- [ ] **Step 3: Implement the rekey in `src/carouauto/db.py`**

Replace the file's contents in full:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_db.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add src/carouauto/db.py tests/test_db.py
git commit -m "feat: re-key SeenStore by search_id for multi-user subscriptions"
```

---

## Task 4: Filter functions (`filters.py`)

**Files:**
- Create: `src/carouauto/filters.py`
- Create: `tests/test_filters.py`

**Interfaces:**
- Consumes: `carouauto.models.Listing` (Task 1, with `condition`).
- Produces: `carouauto.filters.parse_price(price_text: str) -> float | None`,
  `carouauto.filters.passes_filters(listing: Listing, min_price: float | None,
  max_price: float | None, exclude_keywords: str | None, condition_filter: str | None) -> bool`
  — used by the scheduler (Task 11) to decide whether a genuinely-new
  listing gets notified to a given subscriber.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_filters.py`:

```python
from carouauto.filters import parse_price, passes_filters
from carouauto.models import Listing


def make_listing(price="S$100", title="Item", condition="Well used"):
    return Listing(
        listing_id="1",
        title=title,
        price=price,
        url="https://example.com/1",
        thumbnail_url="",
        posted_text="1 minute ago",
        condition=condition,
    )


def test_parse_price_extracts_the_number():
    assert parse_price("S$599") == 599.0
    assert parse_price("S$1,250.50") == 1250.50


def test_parse_price_returns_none_for_unparseable_text():
    assert parse_price("") is None
    assert parse_price("Free") is None


def test_passes_filters_with_no_filters_set_always_true():
    listing = make_listing()
    assert passes_filters(listing, None, None, None, None) is True


def test_price_filter_excludes_outside_range():
    listing = make_listing(price="S$50")
    assert passes_filters(listing, 100.0, 500.0, None, None) is False
    assert passes_filters(listing, 10.0, 100.0, None, None) is True


def test_price_filter_does_not_exclude_unparseable_price():
    listing = make_listing(price="Contact for price")
    assert passes_filters(listing, 100.0, 500.0, None, None) is True


def test_exclude_keywords_matches_case_insensitively_in_title():
    listing = make_listing(title="Nintendo Switch CASE only, no console")
    assert passes_filters(listing, None, None, "case", None) is False
    assert passes_filters(listing, None, None, "box,manual", None) is True


def test_condition_filter_requires_exact_case_insensitive_match():
    listing = make_listing(condition="Brand new")
    assert passes_filters(listing, None, None, None, "brand new") is True
    assert passes_filters(listing, None, None, None, "Well used") is False


def test_all_filters_combine_with_and():
    listing = make_listing(price="S$50", title="Item with case", condition="Well used")
    assert passes_filters(listing, 10.0, 100.0, "case", "Well used") is False
    assert passes_filters(listing, 10.0, 100.0, "box", "Well used") is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_filters.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'carouauto.filters'`

- [ ] **Step 3: Implement `src/carouauto/filters.py`**

```python
from __future__ import annotations

import re

from .models import Listing

_PRICE_RE = re.compile(r"[\d,]+(?:\.\d+)?")


def parse_price(price_text: str) -> float | None:
    match = _PRICE_RE.search(price_text)
    if not match:
        return None
    return float(match.group(0).replace(",", ""))


def _matches_price(listing: Listing, min_price: float | None, max_price: float | None) -> bool:
    if min_price is None and max_price is None:
        return True
    price = parse_price(listing.price)
    if price is None:
        return True  # can't parse it — don't filter out on an unknown price
    if min_price is not None and price < min_price:
        return False
    if max_price is not None and price > max_price:
        return False
    return True


def _matches_exclude_keywords(listing: Listing, exclude_keywords: str | None) -> bool:
    if not exclude_keywords:
        return True
    title_lower = listing.title.lower()
    for word in exclude_keywords.split(","):
        word = word.strip().lower()
        if word and word in title_lower:
            return False
    return True


def _matches_condition(listing: Listing, condition_filter: str | None) -> bool:
    if not condition_filter:
        return True
    return listing.condition.strip().lower() == condition_filter.strip().lower()


def passes_filters(
    listing: Listing,
    min_price: float | None,
    max_price: float | None,
    exclude_keywords: str | None,
    condition_filter: str | None,
) -> bool:
    return (
        _matches_price(listing, min_price, max_price)
        and _matches_exclude_keywords(listing, exclude_keywords)
        and _matches_condition(listing, condition_filter)
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_filters.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add src/carouauto/filters.py tests/test_filters.py
git commit -m "feat: add price/keyword/condition filter functions"
```

---

## Task 5: Refactor `TelegramNotifier` for per-call `chat_id`, add `send_document`

**Files:**
- Modify: `src/carouauto/notifier.py`
- Modify: `tests/test_notifier.py`

**Interfaces:**
- Consumes: `carouauto.models.Listing` (Task 1).
- Produces: `carouauto.notifier.TelegramNotifier(bot_token: str, client:
  httpx.Client | None = None)` — **`chat_id` is no longer a constructor
  argument**; every method now takes it per call:
  - `send_new_listings(self, chat_id: int, search_name: str, listings: list[Listing]) -> None`
  - `send_alert(self, chat_id: int, text: str) -> None`
  - `send_text(self, chat_id: int, text: str) -> None` (new — used for
    plain command replies by the listener, Task 10)
  - `send_document(self, chat_id: int, file_path: str, filename: str) -> None` (new — used by `/backup`, Task 9)
  `format_message(listing: Listing) -> str` is unchanged.

This is a breaking change to the constructor and every send method's
signature, needed because the bot now sends to many different users
instead of one fixed recipient. Update every existing test accordingly.

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_notifier.py` in full:

```python
import httpx
import pytest

from carouauto.models import Listing
from carouauto.notifier import MAX_MESSAGE_CHARS, TelegramNotifier, format_message

BOT_TOKEN = "123456:SUPER-SECRET-BOT-TOKEN"
CHAT_ID = 999


class FakeClient:
    """Stands in for httpx.Client, recording posts and optionally failing."""

    def __init__(self, fail_times=0):
        self.posts = []
        self._fail_times = fail_times

    def post(self, url, data=None, files=None):
        self.posts.append((url, data, files))
        if len(self.posts) <= self._fail_times:
            raise httpx.ConnectError(f"connection failed for {url}")
        return httpx.Response(200, request=httpx.Request("POST", url))


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    monkeypatch.setattr("carouauto.notifier.time.sleep", lambda _: None)


def make_listing(i, title_len=0):
    return Listing(
        listing_id=str(i),
        title=f"Item {i}" + "x" * title_len,
        price="S$10",
        url=f"https://www.carousell.sg/p/item-{i}/",
        thumbnail_url="",
        posted_text="1 hour ago",
        condition="Well used",
    )


def test_format_message_includes_title_price_time_and_url():
    listing = Listing(
        listing_id="1460198499",
        title="Speediance Gym Monster Smart Home Fitness Machine",
        price="S$599",
        url="https://www.carousell.sg/p/speediance-gym-monster-1460198499/",
        thumbnail_url="https://media.karousell.com/thumb.jpg",
        posted_text="18 hours ago",
        condition="Well used",
    )

    message = format_message(listing)

    assert "Speediance Gym Monster Smart Home Fitness Machine" in message
    assert "S$599" in message
    assert "18 hours ago" in message
    assert "https://www.carousell.sg/p/speediance-gym-monster-1460198499/" in message


def test_format_message_labels_stale_posted_text_as_possibly_bumped():
    listing = Listing(
        listing_id="1", title="Item", price="S$10",
        url="https://www.carousell.sg/p/item-1/", thumbnail_url="",
        posted_text="4 hours ago", condition="Well used",
    )

    message = format_message(listing)

    assert message.startswith("🔁 Possibly re-surfaced/bumped")


def test_format_message_does_not_label_fresh_posted_text():
    listing = Listing(
        listing_id="1", title="Item", price="S$10",
        url="https://www.carousell.sg/p/item-1/", thumbnail_url="",
        posted_text="4 minutes ago", condition="Well used",
    )

    message = format_message(listing)

    assert "🔁" not in message
    assert message.startswith("Item")


def test_format_message_omits_blank_price_and_posted_text():
    listing = Listing(
        listing_id="1", title="Item", price="", url="https://www.carousell.sg/p/item-1/",
        thumbnail_url="", posted_text="", condition="",
    )

    message = format_message(listing)

    assert message == "Item\nhttps://www.carousell.sg/p/item-1/"


def test_send_retries_once_then_succeeds():
    client = FakeClient(fail_times=1)
    notifier = TelegramNotifier(BOT_TOKEN, client=client)

    notifier.send_alert(CHAT_ID, "hello")

    assert len(client.posts) == 2


def test_send_raises_after_one_retry_without_leaking_the_token():
    client = FakeClient(fail_times=99)
    notifier = TelegramNotifier(BOT_TOKEN, client=client)

    with pytest.raises(RuntimeError) as excinfo:
        notifier.send_alert(CHAT_ID, "hello")

    assert len(client.posts) == 2
    assert BOT_TOKEN not in str(excinfo.value)
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__context__ is None


def test_send_text_goes_to_the_given_chat_id():
    client = FakeClient()
    notifier = TelegramNotifier(BOT_TOKEN, client=client)

    notifier.send_text(555, "a reply")

    _, data, _ = client.posts[0]
    assert data["chat_id"] == 555
    assert data["text"] == "a reply"


def test_large_batch_is_split_into_messages_under_the_size_limit():
    client = FakeClient()
    notifier = TelegramNotifier(BOT_TOKEN, client=client)
    listings = [make_listing(i, title_len=200) for i in range(60)]

    notifier.send_new_listings(CHAT_ID, "speediance", listings)

    assert len(client.posts) > 1
    for _, data, _ in client.posts:
        assert len(data["text"]) <= MAX_MESSAGE_CHARS
    assert client.posts[0][1]["text"].startswith("60 new listings for 'speediance':")
    for _, data, _ in client.posts[1:]:
        assert "new listings for" not in data["text"]
    combined = "".join(data["text"] for _, data, _ in client.posts)
    for listing in listings:
        assert listing.url in combined


def test_small_batch_still_sends_a_single_message():
    client = FakeClient()
    notifier = TelegramNotifier(BOT_TOKEN, client=client)

    notifier.send_new_listings(CHAT_ID, "speediance", [make_listing(1), make_listing(2)])

    assert len(client.posts) == 1
    assert client.posts[0][1]["text"].startswith("2 new listings for 'speediance':")


def test_send_document_posts_the_file_with_the_given_chat_id(tmp_path):
    client = FakeClient()
    notifier = TelegramNotifier(BOT_TOKEN, client=client)
    file_path = tmp_path / "backup.sqlite3"
    file_path.write_bytes(b"fake sqlite bytes")

    notifier.send_document(777, str(file_path), "carouauto_backup.sqlite3")

    url, data, files = client.posts[0]
    assert url.endswith("/sendDocument")
    assert data["chat_id"] == 777
    assert "document" in files
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_notifier.py -v`
Expected: FAIL — `TypeError: TelegramNotifier.__init__() missing 1
required positional argument` (or similar, since the constructor/method
signatures no longer match).

- [ ] **Step 3: Implement the refactor in `src/carouauto/notifier.py`**

Replace the file's contents in full:

```python
from __future__ import annotations

import time

import httpx

from .models import Listing

TELEGRAM_API_BASE = "https://api.telegram.org"

MAX_MESSAGE_CHARS = 4000
RETRY_BACKOFF_SECONDS = 2

_STALE_AGE_MARKERS = ("hour", "day", "week", "month", "year")


def _looks_stale(posted_text: str) -> bool:
    lowered = posted_text.lower()
    return any(marker in lowered for marker in _STALE_AGE_MARKERS)


def format_message(listing: Listing) -> str:
    lines = []
    if listing.posted_text and _looks_stale(listing.posted_text):
        lines.append("🔁 Possibly re-surfaced/bumped (not a fresh post)")
    lines.append(listing.title)
    if listing.price:
        lines.append(listing.price)
    if listing.posted_text:
        lines.append(listing.posted_text)
    lines.append(listing.url)
    return "\n".join(lines)


class TelegramNotifier:
    def __init__(self, bot_token: str, client: httpx.Client | None = None):
        self._bot_token = bot_token
        self._client = client or httpx.Client(timeout=10.0)

    def send_new_listings(self, chat_id: int, search_name: str, listings: list[Listing]) -> None:
        if not listings:
            return
        if len(listings) == 1:
            self._send_text(
                chat_id, f"New listing for '{search_name}':\n\n{format_message(listings[0])}"
            )
            return

        header = f"{len(listings)} new listings for '{search_name}':"
        prefix = f"{header}\n\n"
        chunk: list[str] = []
        chunk_len = len(prefix)
        for listing in listings:
            body = format_message(listing)
            added = len(body) + (2 if chunk else 0)
            if chunk and chunk_len + added > MAX_MESSAGE_CHARS:
                self._send_text(chat_id, prefix + "\n\n".join(chunk))
                prefix = ""
                chunk = [body]
                chunk_len = len(body)
            else:
                chunk.append(body)
                chunk_len += added
        if chunk:
            self._send_text(chat_id, prefix + "\n\n".join(chunk))

    def send_alert(self, chat_id: int, text: str) -> None:
        self._send_text(chat_id, text)

    def send_text(self, chat_id: int, text: str) -> None:
        self._send_text(chat_id, text)

    def send_document(self, chat_id: int, file_path: str, filename: str) -> None:
        url = f"{TELEGRAM_API_BASE}/bot{self._bot_token}/sendDocument"
        with open(file_path, "rb") as f:
            files = {"document": (filename, f)}
            try:
                response = self._client.post(url, data={"chat_id": chat_id}, files=files)
                response.raise_for_status()
            except httpx.HTTPError as e:
                raise RuntimeError(f"Telegram document send failed: {type(e).__name__}") from None

    def _send_text(self, chat_id: int, text: str) -> None:
        url = f"{TELEGRAM_API_BASE}/bot{self._bot_token}/sendMessage"
        failure_name = ""
        for attempt in range(2):
            try:
                response = self._client.post(url, data={"chat_id": chat_id, "text": text})
                response.raise_for_status()
                return
            except httpx.HTTPError as e:
                failure_name = type(e).__name__
                if attempt == 0:
                    time.sleep(RETRY_BACKOFF_SECONDS)
        raise RuntimeError(f"Telegram send failed after retry: {failure_name}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_notifier.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add src/carouauto/notifier.py tests/test_notifier.py
git commit -m "feat: make TelegramNotifier multi-recipient, add send_document"
```

---

## Task 6: Command parsing, `BotContext`, and `/start` `/register` `/help`

**Files:**
- Create: `src/carouauto/commands.py`
- Create: `tests/test_commands.py`

**Interfaces:**
- Consumes: `SubscriptionStore` (Task 2), `SeenStore` (Task 3),
  `TelegramNotifier` (Task 5), `FetchHtmlFn` type (matches
  `Poller.fetch_search_html`'s shape from the existing `scheduler.py`),
  `SearchState` (existing `scheduler.py`, read-only here).
- Produces: `carouauto.commands.parse_command(text: str) -> tuple[str,
  list[str]] | None`, `carouauto.commands.BotContext` (dataclass —
  `subscriptions: SubscriptionStore`, `seen_store: SeenStore`,
  `notifier: TelegramNotifier`, `registration_password: str`,
  `fetch_html: FetchHtmlFn`, `states: dict[str, SearchState]`,
  `db_path: str`, `poll_interval_seconds: float`), and the async handler
  functions `handle_start`, `handle_register`, `handle_help` — each
  `async def handle_x(args: list[str], chat_id: int, ctx: BotContext) -> str`.
  Later tasks (7, 8, 9) **append** more handlers to this same file, and
  Task 9 adds the `dispatch()` function that ties them all together.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_commands.py`:

```python
import pytest

from carouauto.commands import BotContext, handle_help, handle_register, handle_start, parse_command
from carouauto.db import SeenStore
from carouauto.subscriptions import SubscriptionStore

REGISTRATION_PASSWORD = "let-me-in"


def make_ctx(tmp_path):
    return BotContext(
        subscriptions=SubscriptionStore(str(tmp_path / "t.sqlite3")),
        seen_store=SeenStore(str(tmp_path / "t.sqlite3")),
        notifier=None,
        registration_password=REGISTRATION_PASSWORD,
        fetch_html=None,
        states={},
        db_path=str(tmp_path / "t.sqlite3"),
        poll_interval_seconds=300,
    )


def test_parse_command_extracts_command_and_args():
    assert parse_command("/add speediance https://example.com 100 500") == (
        "add",
        ["speediance", "https://example.com", "100", "500"],
    )


def test_parse_command_strips_leading_slash_and_lowercases():
    assert parse_command("/HELP") == ("help", [])


def test_parse_command_strips_botname_suffix():
    assert parse_command("/help@carouauto_bot") == ("help", [])


def test_parse_command_returns_none_for_non_command_text():
    assert parse_command("hello there") is None


def test_parse_command_returns_none_for_empty_text():
    assert parse_command("") is None
    assert parse_command("/") is None


@pytest.mark.asyncio
async def test_handle_start_mentions_register(tmp_path):
    ctx = make_ctx(tmp_path)

    reply = await handle_start([], 111, ctx)

    assert "/register" in reply


@pytest.mark.asyncio
async def test_handle_register_with_correct_password_succeeds(tmp_path):
    ctx = make_ctx(tmp_path)

    reply = await handle_register([REGISTRATION_PASSWORD], 111, ctx)

    assert ctx.subscriptions.is_active(111) is True
    assert "Registered" in reply or "registered" in reply.lower()


@pytest.mark.asyncio
async def test_handle_register_with_wrong_password_fails(tmp_path):
    ctx = make_ctx(tmp_path)

    reply = await handle_register(["wrong-password"], 111, ctx)

    assert ctx.subscriptions.is_active(111) is False
    assert "incorrect" in reply.lower()


@pytest.mark.asyncio
async def test_handle_register_with_no_args_gives_usage(tmp_path):
    ctx = make_ctx(tmp_path)

    reply = await handle_register([], 111, ctx)

    assert "usage" in reply.lower()


@pytest.mark.asyncio
async def test_handle_register_twice_reports_already_registered(tmp_path):
    ctx = make_ctx(tmp_path)
    await handle_register([REGISTRATION_PASSWORD], 111, ctx)

    reply = await handle_register([REGISTRATION_PASSWORD], 111, ctx)

    assert "already" in reply.lower()


@pytest.mark.asyncio
async def test_handle_help_mentions_the_poll_interval(tmp_path):
    ctx = make_ctx(tmp_path)

    reply = await handle_help([], 111, ctx)

    assert "5 minute" in reply.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_commands.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'carouauto.commands'`

- [ ] **Step 3: Implement `src/carouauto/commands.py`**

```python
from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Awaitable, Callable

from .db import SeenStore
from .notifier import TelegramNotifier
from .scheduler import SearchState
from .subscriptions import SubscriptionStore

FetchHtmlFn = Callable[[str], Awaitable[str]]


@dataclass
class BotContext:
    subscriptions: SubscriptionStore
    seen_store: SeenStore
    notifier: TelegramNotifier
    registration_password: str
    fetch_html: FetchHtmlFn
    states: dict[str, SearchState]
    db_path: str
    poll_interval_seconds: float


def parse_command(text: str) -> tuple[str, list[str]] | None:
    text = text.strip()
    if not text.startswith("/"):
        return None
    try:
        parts = shlex.split(text)
    except ValueError:
        parts = text.split()
    if not parts or len(parts[0]) <= 1:
        return None
    command = parts[0][1:].lower().split("@")[0]
    return command, parts[1:]


async def handle_start(args: list[str], chat_id: int, ctx: BotContext) -> str:
    return (
        "Welcome to carouauto — a Carousell listing monitor.\n"
        "Send /register <password> to get started, then /help for what you can do."
    )


async def handle_register(args: list[str], chat_id: int, ctx: BotContext) -> str:
    if len(args) != 1:
        return "Usage: /register <password>"
    if args[0] != ctx.registration_password:
        return "Incorrect password."
    if ctx.subscriptions.register(chat_id):
        return "Registered! Try /add <name> <url> to track a search. See /help for all commands."
    return "You're already registered."


async def handle_help(args: list[str], chat_id: int, ctx: BotContext) -> str:
    minutes = round(ctx.poll_interval_seconds / 60)
    return (
        "carouauto — Carousell listing monitor\n\n"
        f"Checks run roughly every {minutes} minutes — expect new listings "
        "a few minutes after they're posted, not instantly.\n\n"
        "/register <password> — get access\n"
        "/add <name> <url> [min] [max] — track a search, optional price range\n"
        "/remove <name> — stop tracking a search\n"
        "/searches — list your tracked searches\n"
        "/setprice <name> <min> <max> — update a search's price filter\n"
        "/setexclude <name> <word1,word2,...> — exclude listings matching these words (or 'none')\n"
        "/setcondition <name> <condition> — only notify for this condition (or 'any')\n"
        "/pause <name> / /resume <name> — stop/resume notifications for a search\n"
        "/status — check your searches' last-poll status\n"
        "/list <name> — see what's currently on Carousell for a search right now\n"
        "/revoke <chat_id> — admin only, remove someone's access\n"
        "/backup — admin only, get a copy of the database\n"
        "/help — this message"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_commands.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add src/carouauto/commands.py tests/test_commands.py
git commit -m "feat: add command parser, BotContext, and start/register/help handlers"
```

---

## Task 7: Search-management command handlers

**Files:**
- Modify: `src/carouauto/commands.py`
- Modify: `tests/test_commands.py`

**Interfaces:**
- Consumes: `BotContext`, `SubscriptionStore` methods (Task 2),
  `SeenStore.delete_all_for_search` (Task 3) — all from Task 6's file.
- Produces: `async def handle_add`, `handle_remove`, `handle_searches`,
  `handle_setprice`, `handle_setexclude`, `handle_setcondition`,
  `handle_pause`, `handle_resume` — each the same
  `(args, chat_id, ctx) -> str` shape as Task 6's handlers, appended to
  `src/carouauto/commands.py`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_commands.py`:

```python
from carouauto.commands import (
    handle_add,
    handle_pause,
    handle_remove,
    handle_resume,
    handle_searches,
    handle_setcondition,
    handle_setexclude,
    handle_setprice,
)


@pytest.mark.asyncio
async def test_handle_add_creates_a_search(tmp_path):
    ctx = make_ctx(tmp_path)

    reply = await handle_add(["speediance", "https://example.com/s"], 111, ctx)

    assert "speediance" in reply
    found = ctx.subscriptions.get_search(111, "speediance")
    assert found.url == "https://example.com/s"
    assert found.min_price is None


@pytest.mark.asyncio
async def test_handle_add_with_price_range(tmp_path):
    ctx = make_ctx(tmp_path)

    await handle_add(["speediance", "https://example.com/s", "100", "500"], 111, ctx)

    found = ctx.subscriptions.get_search(111, "speediance")
    assert (found.min_price, found.max_price) == (100.0, 500.0)


@pytest.mark.asyncio
async def test_handle_add_rejects_non_numeric_price(tmp_path):
    ctx = make_ctx(tmp_path)

    reply = await handle_add(["speediance", "https://example.com/s", "not-a-number"], 111, ctx)

    assert "number" in reply.lower()
    assert ctx.subscriptions.get_search(111, "speediance") is None


@pytest.mark.asyncio
async def test_handle_add_duplicate_name_fails_clearly(tmp_path):
    ctx = make_ctx(tmp_path)
    await handle_add(["speediance", "https://example.com/s"], 111, ctx)

    reply = await handle_add(["speediance", "https://example.com/other"], 111, ctx)

    assert "already" in reply.lower()


@pytest.mark.asyncio
async def test_handle_add_too_few_args_gives_usage(tmp_path):
    ctx = make_ctx(tmp_path)

    reply = await handle_add(["speediance"], 111, ctx)

    assert "usage" in reply.lower()


@pytest.mark.asyncio
async def test_handle_remove_deletes_search_and_its_seen_history(tmp_path):
    ctx = make_ctx(tmp_path)
    await handle_add(["speediance", "https://example.com/s"], 111, ctx)
    search_id = ctx.subscriptions.get_search(111, "speediance").search_id
    ctx.seen_store.mark_seen(search_id, ["1"])

    reply = await handle_remove(["speediance"], 111, ctx)

    assert "removed" in reply.lower()
    assert ctx.subscriptions.get_search(111, "speediance") is None
    assert ctx.seen_store.get_new_ids(search_id, ["1"]) == []  # history gone -> first-run again


@pytest.mark.asyncio
async def test_handle_remove_unknown_search(tmp_path):
    ctx = make_ctx(tmp_path)

    reply = await handle_remove(["does-not-exist"], 111, ctx)

    assert "no search" in reply.lower()


@pytest.mark.asyncio
async def test_handle_searches_lists_names_and_filters(tmp_path):
    ctx = make_ctx(tmp_path)
    await handle_add(["speediance", "https://example.com/s", "100", "500"], 111, ctx)

    reply = await handle_searches([], 111, ctx)

    assert "speediance" in reply
    assert "100" in reply and "500" in reply


@pytest.mark.asyncio
async def test_handle_searches_with_none_tracked(tmp_path):
    ctx = make_ctx(tmp_path)

    reply = await handle_searches([], 111, ctx)

    assert "no tracked searches" in reply.lower()


@pytest.mark.asyncio
async def test_handle_setprice_updates_an_existing_search(tmp_path):
    ctx = make_ctx(tmp_path)
    await handle_add(["speediance", "https://example.com/s"], 111, ctx)

    reply = await handle_setprice(["speediance", "50", "200"], 111, ctx)

    assert "updated" in reply.lower()
    found = ctx.subscriptions.get_search(111, "speediance")
    assert (found.min_price, found.max_price) == (50.0, 200.0)


@pytest.mark.asyncio
async def test_handle_setexclude_sets_and_clears(tmp_path):
    ctx = make_ctx(tmp_path)
    await handle_add(["speediance", "https://example.com/s"], 111, ctx)

    await handle_setexclude(["speediance", "case,box"], 111, ctx)
    assert ctx.subscriptions.get_search(111, "speediance").exclude_keywords == "case,box"

    await handle_setexclude(["speediance", "none"], 111, ctx)
    assert ctx.subscriptions.get_search(111, "speediance").exclude_keywords is None


@pytest.mark.asyncio
async def test_handle_setcondition_sets_and_clears(tmp_path):
    ctx = make_ctx(tmp_path)
    await handle_add(["speediance", "https://example.com/s"], 111, ctx)

    await handle_setcondition(["speediance", "Brand", "new"], 111, ctx)
    assert ctx.subscriptions.get_search(111, "speediance").condition_filter == "Brand new"

    await handle_setcondition(["speediance", "any"], 111, ctx)
    assert ctx.subscriptions.get_search(111, "speediance").condition_filter is None


@pytest.mark.asyncio
async def test_handle_pause_and_resume(tmp_path):
    ctx = make_ctx(tmp_path)
    await handle_add(["speediance", "https://example.com/s"], 111, ctx)

    await handle_pause(["speediance"], 111, ctx)
    assert ctx.subscriptions.get_search(111, "speediance").paused is True

    await handle_resume(["speediance"], 111, ctx)
    assert ctx.subscriptions.get_search(111, "speediance").paused is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_commands.py -v`
Expected: FAIL — `ImportError: cannot import name 'handle_add'`

- [ ] **Step 3: Append the handlers to `src/carouauto/commands.py`**

Add at the end of the file:

```python
async def handle_add(args: list[str], chat_id: int, ctx: BotContext) -> str:
    if len(args) < 2:
        return "Usage: /add <name> <url> [min] [max]"
    name, url = args[0], args[1]
    min_price = max_price = None
    if len(args) >= 3:
        try:
            min_price = float(args[2])
        except ValueError:
            return "min price must be a number."
    if len(args) >= 4:
        try:
            max_price = float(args[3])
        except ValueError:
            return "max price must be a number."
    search_id = ctx.subscriptions.add_search(chat_id, name, url, min_price, max_price)
    if search_id is None:
        return f"You already have a search named '{name}'. Use /remove first or pick a different name."
    minutes = round(ctx.poll_interval_seconds / 60)
    return f"Added '{name}'. You'll be notified of new listings within ~{minutes} minutes of the next check."


async def handle_remove(args: list[str], chat_id: int, ctx: BotContext) -> str:
    if len(args) != 1:
        return "Usage: /remove <name>"
    search_id = ctx.subscriptions.remove_search(chat_id, args[0])
    if search_id is None:
        return f"No search named '{args[0]}'."
    ctx.seen_store.delete_all_for_search(search_id)
    return f"Removed '{args[0]}'."


async def handle_searches(args: list[str], chat_id: int, ctx: BotContext) -> str:
    subs = ctx.subscriptions.list_searches(chat_id)
    if not subs:
        return "You have no tracked searches yet. Use /add <name> <url> to start one."
    lines = []
    for sub in subs:
        parts = [sub.name]
        if sub.min_price is not None or sub.max_price is not None:
            lo = sub.min_price if sub.min_price is not None else "-"
            hi = sub.max_price if sub.max_price is not None else "-"
            parts.append(f"price {lo}-{hi}")
        if sub.exclude_keywords:
            parts.append(f"excluding: {sub.exclude_keywords}")
        if sub.condition_filter:
            parts.append(f"condition: {sub.condition_filter}")
        if sub.paused:
            parts.append("(paused)")
        lines.append(" | ".join(parts))
    return "\n".join(lines)


async def handle_setprice(args: list[str], chat_id: int, ctx: BotContext) -> str:
    if len(args) != 3:
        return "Usage: /setprice <name> <min> <max>"
    name, min_s, max_s = args
    try:
        min_price = float(min_s)
        max_price = float(max_s)
    except ValueError:
        return "min and max must be numbers."
    if not ctx.subscriptions.set_price_filter(chat_id, name, min_price, max_price):
        return f"No search named '{name}'."
    return f"Updated price filter for '{name}'."


async def handle_setexclude(args: list[str], chat_id: int, ctx: BotContext) -> str:
    if len(args) < 2:
        return "Usage: /setexclude <name> <word1,word2,...> (or 'none')"
    name = args[0]
    value = " ".join(args[1:]).strip()
    exclude = None if value.lower() == "none" else value
    if not ctx.subscriptions.set_exclude_keywords(chat_id, name, exclude):
        return f"No search named '{name}'."
    return f"Updated exclude list for '{name}'." if exclude else f"Cleared exclude list for '{name}'."


async def handle_setcondition(args: list[str], chat_id: int, ctx: BotContext) -> str:
    if len(args) < 2:
        return "Usage: /setcondition <name> <condition> (or 'any')"
    name = args[0]
    value = " ".join(args[1:]).strip()
    condition = None if value.lower() == "any" else value
    if not ctx.subscriptions.set_condition_filter(chat_id, name, condition):
        return f"No search named '{name}'."
    return (
        f"Set condition filter for '{name}' to '{condition}'."
        if condition
        else f"Cleared condition filter for '{name}'."
    )


async def handle_pause(args: list[str], chat_id: int, ctx: BotContext) -> str:
    if len(args) != 1:
        return "Usage: /pause <name>"
    if not ctx.subscriptions.set_paused(chat_id, args[0], True):
        return f"No search named '{args[0]}'."
    return f"Paused '{args[0]}'."


async def handle_resume(args: list[str], chat_id: int, ctx: BotContext) -> str:
    if len(args) != 1:
        return "Usage: /resume <name>"
    if not ctx.subscriptions.set_paused(chat_id, args[0], False):
        return f"No search named '{args[0]}'."
    return f"Resumed '{args[0]}'."
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_commands.py -v`
Expected: PASS (24 tests)

- [ ] **Step 5: Commit**

```bash
git add src/carouauto/commands.py tests/test_commands.py
git commit -m "feat: add search-management command handlers (add/remove/searches/setprice/setexclude/setcondition/pause/resume)"
```

---

## Task 8: `/status` and `/list` command handlers

**Files:**
- Modify: `src/carouauto/commands.py`
- Modify: `tests/test_commands.py`

**Interfaces:**
- Consumes: `SearchState` (existing `scheduler.py`, read via
  `ctx.states`), `is_challenge_page` (existing `challenge.py`),
  `parse_listings` (existing `parser.py`), `format_message` (Task 5).
- Produces: `async def handle_status`, `handle_list` — appended to
  `src/carouauto/commands.py`. `handle_list` is the only command handler
  that awaits I/O (`ctx.fetch_html`).

`/list` always shows the current page unfiltered — per the spec, filters
only gate automatic notifications, never this on-demand snapshot — and it
never touches `seen_listings` (no call to `get_new_ids`/`mark_seen`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_commands.py`:

```python
from datetime import datetime, timezone

from carouauto.commands import handle_list, handle_status
from carouauto.scheduler import SearchState

NORMAL_HTML = """
<div data-testid="listing-card-1">
  <a href="/u/seller1/"><p data-testid="listing-card-text-seller-name">seller1</p><div><p>2 minutes ago</p></div></a>
  <a href="/p/item-one-1/"><img alt="Item One" src="https://example.com/1.jpg"/><p>Item One</p><div><p title="S$10">S$10</p></div><p>Brand new</p></a>
</div>
"""

CHALLENGE_HTML = "<html><head><title>Just a moment...</title></head></html>"


@pytest.mark.asyncio
async def test_handle_status_with_no_searches(tmp_path):
    ctx = make_ctx(tmp_path)

    reply = await handle_status([], 111, ctx)

    assert "no tracked searches" in reply.lower()


@pytest.mark.asyncio
async def test_handle_status_reports_paused_by_user(tmp_path):
    ctx = make_ctx(tmp_path)
    await handle_add(["speediance", "https://example.com/s"], 111, ctx)
    await handle_pause(["speediance"], 111, ctx)

    reply = await handle_status([], 111, ctx)

    assert "speediance" in reply
    assert "paused" in reply.lower()
    assert "you" in reply.lower()


@pytest.mark.asyncio
async def test_handle_status_reports_cloudflare_pause(tmp_path):
    ctx = make_ctx(tmp_path)
    await handle_add(["speediance", "https://example.com/s"], 111, ctx)
    ctx.states["https://example.com/s"] = SearchState(paused=True)

    reply = await handle_status([], 111, ctx)

    assert "challenge" in reply.lower()


@pytest.mark.asyncio
async def test_handle_status_reports_last_polled_time(tmp_path):
    ctx = make_ctx(tmp_path)
    await handle_add(["speediance", "https://example.com/s"], 111, ctx)
    ctx.states["https://example.com/s"] = SearchState(
        last_polled_at=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    )

    reply = await handle_status([], 111, ctx)

    assert "12:00" in reply


@pytest.mark.asyncio
async def test_handle_list_shows_current_listings_unfiltered(tmp_path):
    ctx = make_ctx(tmp_path)
    await handle_add(["speediance", "https://example.com/s", "1000", "2000"], 111, ctx)  # price filter that would exclude Item One

    async def fetch_html(url):
        return NORMAL_HTML

    ctx.fetch_html = fetch_html

    reply = await handle_list(["speediance"], 111, ctx)

    assert "Item One" in reply  # shown despite the S$10 price failing the S$1000-2000 filter


@pytest.mark.asyncio
async def test_handle_list_does_not_touch_seen_state(tmp_path):
    ctx = make_ctx(tmp_path)
    await handle_add(["speediance", "https://example.com/s"], 111, ctx)
    search_id = ctx.subscriptions.get_search(111, "speediance").search_id

    async def fetch_html(url):
        return NORMAL_HTML

    ctx.fetch_html = fetch_html
    await handle_list(["speediance"], 111, ctx)

    # If /list had marked "1" seen, this would return [] instead.
    assert ctx.seen_store.get_new_ids(search_id, ["1"]) == []  # still first-run: untouched


@pytest.mark.asyncio
async def test_handle_list_reports_challenge_page(tmp_path):
    ctx = make_ctx(tmp_path)
    await handle_add(["speediance", "https://example.com/s"], 111, ctx)

    async def fetch_html(url):
        return CHALLENGE_HTML

    ctx.fetch_html = fetch_html

    reply = await handle_list(["speediance"], 111, ctx)

    assert "challenge" in reply.lower()


@pytest.mark.asyncio
async def test_handle_list_unknown_search(tmp_path):
    ctx = make_ctx(tmp_path)

    reply = await handle_list(["does-not-exist"], 111, ctx)

    assert "no search" in reply.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_commands.py -v`
Expected: FAIL — `ImportError: cannot import name 'handle_status'`

- [ ] **Step 3: Append to `src/carouauto/commands.py`**

Add the needed imports near the top (alongside the existing ones):

```python
from .challenge import is_challenge_page
from .parser import parse_listings
```

And append at the end of the file:

```python
LIST_CAP = 15


async def handle_status(args: list[str], chat_id: int, ctx: BotContext) -> str:
    subs = ctx.subscriptions.list_searches(chat_id)
    if not subs:
        return "You have no tracked searches. Use /add <name> <url> to start one."
    minutes = round(ctx.poll_interval_seconds / 60)
    lines = [f"Poll interval: ~{minutes} minutes", ""]
    for sub in subs:
        state = ctx.states.get(sub.url)
        if sub.paused:
            status_text = "paused (by you)"
        elif state and state.paused:
            status_text = "paused (Cloudflare challenge, awaiting manual solve)"
        elif state and state.last_polled_at:
            status_text = f"last checked {state.last_polled_at.strftime('%H:%M UTC')}"
        else:
            status_text = "not yet checked"
        lines.append(f"{sub.name}: {status_text}")
    return "\n".join(lines)


async def handle_list(args: list[str], chat_id: int, ctx: BotContext) -> str:
    if len(args) != 1:
        return "Usage: /list <name>"
    name = args[0]
    sub = ctx.subscriptions.get_search(chat_id, name)
    if sub is None:
        return f"No search named '{name}'. Use /searches to see your tracked searches."
    html = await ctx.fetch_html(sub.url)
    if is_challenge_page(html):
        return "Carousell is showing a Cloudflare challenge right now — try again shortly."
    listings = parse_listings(html)
    if not listings:
        return f"No listings currently found for '{name}'."
    lines = [f"Current listings for '{name}' (showing up to {LIST_CAP}):", ""]
    for listing in listings[:LIST_CAP]:
        lines.append(format_message(listing))
        lines.append("")
    return "\n".join(lines).strip()
```

Also add `format_message` to the existing `.notifier` import at the top
of the file (it currently only imports `TelegramNotifier`):

```python
from .notifier import TelegramNotifier, format_message
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_commands.py -v`
Expected: PASS (31 tests)

- [ ] **Step 5: Commit**

```bash
git add src/carouauto/commands.py tests/test_commands.py
git commit -m "feat: add /status and /list command handlers"
```

---

## Task 9: `/revoke`, `/backup`, and the `dispatch()` router

**Files:**
- Modify: `src/carouauto/commands.py`
- Modify: `tests/test_commands.py`

**Interfaces:**
- Consumes: every handler from Tasks 6-8, `SubscriptionStore.is_admin`/`revoke`
  (Task 2), `TelegramNotifier.send_document` (Task 5).
- Produces: `async def handle_revoke`, `handle_backup`, and
  `async def dispatch(command: str, args: list[str], chat_id: int, ctx:
  BotContext) -> str` — the single entry point Task 10's listener calls.
  `dispatch` never raises: it centralizes the auth check (unregistered →
  "register first", non-admin on an admin command → "admin-only") and
  wraps every handler call in a try/except, per the spec's error-handling
  section.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_commands.py`:

```python
from carouauto.commands import dispatch, handle_backup, handle_revoke


@pytest.mark.asyncio
async def test_handle_revoke_by_chat_id(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(222)

    reply = await handle_revoke(["222"], 111, ctx)

    assert "revoked" in reply.lower()
    assert ctx.subscriptions.is_active(222) is False


@pytest.mark.asyncio
async def test_handle_revoke_unknown_chat_id(tmp_path):
    ctx = make_ctx(tmp_path)

    reply = await handle_revoke(["999"], 111, ctx)

    assert "no user" in reply.lower()


@pytest.mark.asyncio
async def test_handle_revoke_non_numeric(tmp_path):
    ctx = make_ctx(tmp_path)

    reply = await handle_revoke(["not-a-number"], 111, ctx)

    assert "number" in reply.lower()


@pytest.mark.asyncio
async def test_handle_backup_sends_a_document(tmp_path):
    ctx = make_ctx(tmp_path)
    sent = []

    class FakeNotifier:
        def send_document(self, chat_id, file_path, filename):
            sent.append((chat_id, filename))

    ctx.notifier = FakeNotifier()

    reply = await handle_backup([], 111, ctx)

    assert sent == [(111, "carouauto_backup.sqlite3")]
    assert reply != ""


@pytest.mark.asyncio
async def test_dispatch_rejects_unregistered_user(tmp_path):
    ctx = make_ctx(tmp_path)

    reply = await dispatch("add", ["speediance", "https://example.com"], 111, ctx)

    assert "register" in reply.lower()


@pytest.mark.asyncio
async def test_dispatch_allows_public_commands_without_registration(tmp_path):
    ctx = make_ctx(tmp_path)

    reply = await dispatch("help", [], 111, ctx)

    assert "carouauto" in reply.lower()


@pytest.mark.asyncio
async def test_dispatch_allows_registered_user_to_add(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)

    reply = await dispatch("add", ["speediance", "https://example.com"], 111, ctx)

    assert "added" in reply.lower()


@pytest.mark.asyncio
async def test_dispatch_rejects_non_admin_on_admin_command(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)

    reply = await dispatch("revoke", ["222"], 111, ctx)

    assert "admin" in reply.lower()


@pytest.mark.asyncio
async def test_dispatch_allows_admin_on_admin_command(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.seed_admin(111)
    ctx.subscriptions.register(222)

    reply = await dispatch("revoke", ["222"], 111, ctx)

    assert "revoked" in reply.lower()


@pytest.mark.asyncio
async def test_dispatch_unknown_command(tmp_path):
    ctx = make_ctx(tmp_path)

    reply = await dispatch("not-a-real-command", [], 111, ctx)

    assert "unknown command" in reply.lower()


@pytest.mark.asyncio
async def test_dispatch_never_raises_even_if_a_handler_blows_up(tmp_path, monkeypatch):
    import carouauto.commands as commands_module

    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)

    async def broken_handler(args, chat_id, ctx):
        raise RuntimeError("boom")

    monkeypatch.setitem(commands_module._HANDLERS, "add", broken_handler)

    reply = await dispatch("add", [], 111, ctx)

    assert "something went wrong" in reply.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_commands.py -v`
Expected: FAIL — `ImportError: cannot import name 'handle_revoke'`

- [ ] **Step 3: Append to `src/carouauto/commands.py`**

Add `import logging` and `import sqlite3`, `import tempfile`, `import os`
near the top alongside the existing imports, plus a module logger:

```python
import logging
import os
import sqlite3
import tempfile

logger = logging.getLogger("carouauto")
```

Append at the end of the file:

```python
async def handle_revoke(args: list[str], chat_id: int, ctx: BotContext) -> str:
    if len(args) != 1:
        return "Usage: /revoke <chat_id>"
    try:
        target = int(args[0])
    except ValueError:
        return "chat_id must be a number."
    if not ctx.subscriptions.revoke(target):
        return f"No user with chat_id {target}."
    return f"Revoked access for {target}."


async def handle_backup(args: list[str], chat_id: int, ctx: BotContext) -> str:
    fd, tmp_path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    try:
        source = sqlite3.connect(ctx.db_path)
        dest = sqlite3.connect(tmp_path)
        source.backup(dest)
        dest.close()
        source.close()
        ctx.notifier.send_document(chat_id, tmp_path, "carouauto_backup.sqlite3")
    finally:
        os.remove(tmp_path)
    return "📦 Backup sent above."


_PUBLIC_COMMANDS = {"start", "register", "help"}
_ADMIN_COMMANDS = {"revoke", "backup"}

_HANDLERS = {
    "start": handle_start,
    "register": handle_register,
    "help": handle_help,
    "add": handle_add,
    "remove": handle_remove,
    "searches": handle_searches,
    "setprice": handle_setprice,
    "setexclude": handle_setexclude,
    "setcondition": handle_setcondition,
    "pause": handle_pause,
    "resume": handle_resume,
    "status": handle_status,
    "list": handle_list,
    "revoke": handle_revoke,
    "backup": handle_backup,
}


async def dispatch(command: str, args: list[str], chat_id: int, ctx: BotContext) -> str:
    handler = _HANDLERS.get(command)
    if handler is None:
        return "Unknown command. Send /help for a list of commands."
    if command not in _PUBLIC_COMMANDS:
        if not ctx.subscriptions.is_active(chat_id):
            return "You need to /register first."
        if command in _ADMIN_COMMANDS and not ctx.subscriptions.is_admin(chat_id):
            return "This command is admin-only."
    try:
        return await handler(args, chat_id, ctx)
    except Exception as exc:
        logger.error("error handling /%s: %s", command, exc)
        return "Something went wrong handling that command."
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_commands.py -v`
Expected: PASS (41 tests)

- [ ] **Step 5: Commit**

```bash
git add src/carouauto/commands.py tests/test_commands.py
git commit -m "feat: add /revoke, /backup, and the dispatch() command router"
```

---

## Task 10: Telegram long-polling command listener

**Files:**
- Create: `src/carouauto/telegram_listener.py`

**Interfaces:**
- Consumes: `carouauto.commands.parse_command`, `dispatch`, `BotContext`
  (Task 9).
- Produces: `async def run_command_listener(bot_token: str, ctx:
  BotContext) -> None` — consumed by `main.py` (Task 13), run
  concurrently alongside the poll loop via `asyncio.gather`.

No automated test — this is I/O against the real Telegram API, verified
manually (Step 3 below), consistent with how `poller.py` is handled.

- [ ] **Step 1: Implement `src/carouauto/telegram_listener.py`**

```python
from __future__ import annotations

import asyncio
import logging

import httpx

from .commands import BotContext, dispatch, parse_command

logger = logging.getLogger("carouauto")

TELEGRAM_API_BASE = "https://api.telegram.org"
LONG_POLL_TIMEOUT_SECONDS = 30


async def get_updates(client: httpx.AsyncClient, bot_token: str, offset: int | None) -> list[dict]:
    params: dict[str, int] = {"timeout": LONG_POLL_TIMEOUT_SECONDS}
    if offset is not None:
        params["offset"] = offset
    response = await client.get(
        f"{TELEGRAM_API_BASE}/bot{bot_token}/getUpdates",
        params=params,
        timeout=LONG_POLL_TIMEOUT_SECONDS + 10,
    )
    response.raise_for_status()
    return response.json()["result"]


async def run_command_listener(bot_token: str, ctx: BotContext) -> None:
    offset: int | None = None
    async with httpx.AsyncClient() as client:
        while True:
            try:
                updates = await get_updates(client, bot_token, offset)
            except httpx.HTTPError as exc:
                # httpx embeds the full request URL (which contains the bot
                # token) in its own exception message — never log str(exc).
                logger.error("error polling Telegram updates: %s", type(exc).__name__)
                await asyncio.sleep(5)
                continue
            except Exception as exc:
                logger.error("unexpected error in command listener: %s", exc)
                await asyncio.sleep(5)
                continue

            for update in updates:
                offset = update["update_id"] + 1
                message = update.get("message")
                if not message or "text" not in message:
                    continue
                chat_id = message["chat"]["id"]
                parsed = parse_command(message["text"])
                if parsed is None:
                    continue
                command, args = parsed
                reply = await dispatch(command, args, chat_id, ctx)
                if reply:
                    ctx.notifier.send_text(chat_id, reply)
```

- [ ] **Step 2: Manually verify against a real bot**

This needs `BotContext` wired up with a real `SubscriptionStore`,
`SeenStore`, and `TelegramNotifier` — defer full manual verification to
Task 13 (once `main.py` wires everything together), since that's the
first point a real end-to-end run is possible. Note this explicitly in
the commit message.

- [ ] **Step 3: Commit**

```bash
git add src/carouauto/telegram_listener.py
git commit -m "feat: add Telegram long-polling command listener (manual verification deferred to main.py wiring)"
```

---

## Task 11: Rewrite the scheduler for URL-dedup and per-subscriber fan-out

**Files:**
- Modify: `src/carouauto/scheduler.py`
- Modify: `tests/test_scheduler.py`

**Interfaces:**
- Consumes: `UserSearch` (Task 2), `passes_filters` (Task 4),
  `SeenStore.get_new_ids`/`mark_seen` (Task 3, now `search_id`-keyed).
- Produces: `SearchState` (dataclass, gains `last_polled_at: datetime |
  None = None` — read by `/status`, Task 8), `async def run_cycle_for_url(url: str,
  subscribers: list[UserSearch], state: SearchState, fetch_html:
  FetchHtmlFn, seen_store: SeenStore, notifier, tunnel_instructions: str,
  admin_chat_id: int, now=...) -> None`, `async def run_one_round(subscriptions:
  list[UserSearch], states: dict[str, SearchState], fetch_html, seen_store,
  notifier, tunnel_instructions, admin_chat_id) -> tuple[int, int]`
  (returns `(succeeded_url_count, total_url_count)` — a round with zero
  URLs to poll is `(0, 0)`, never treated as a failure), `async def
  run_forever(get_subscriptions: Callable[[], list[UserSearch]], states,
  fetch_html, seen_store, notifier, tunnel_instructions, admin_chat_id,
  poll_interval_seconds, poll_jitter_fraction) -> None`. This is a
  breaking rewrite of the previous single-search-per-cycle interface —
  `main.py` (Task 13) is updated to match.

Operational alerts (challenge, dead-browser) go to `admin_chat_id`, never
to individual subscribers — they have no VPS access to act on them.

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_scheduler.py` in full:

```python
from datetime import datetime, timedelta, timezone

import pytest

from carouauto.db import SeenStore
from carouauto.subscriptions import UserSearch
from carouauto.scheduler import (
    MAX_CONSECUTIVE_ROUND_FAILURES,
    BrowserLikelyDeadError,
    SearchState,
    run_cycle_for_url,
    run_forever,
    run_one_round,
)

NORMAL_HTML = """
<div data-testid="listing-card-1">
  <a href="/u/seller1/"><p data-testid="listing-card-text-seller-name">seller1</p><div><p>1 hour ago</p></div></a>
  <a href="/p/item-one-1/"><img alt="Item One" src="https://example.com/1.jpg"/><p>Item One</p><div><p title="S$10">S$10</p></div><p>Well used</p></a>
</div>
"""

NORMAL_HTML_WITH_NEW_LISTING = NORMAL_HTML + """
<div data-testid="listing-card-2">
  <a href="/u/seller2/"><p data-testid="listing-card-text-seller-name">seller2</p><div><p>2 minutes ago</p></div></a>
  <a href="/p/item-two-2/"><img alt="Item Two" src="https://example.com/2.jpg"/><p>Item Two</p><div><p title="S$200">S$200</p></div><p>Brand new</p></a>
</div>
"""

CHALLENGE_HTML = "<html><head><title>Just a moment...</title></head></html>"

ADMIN_CHAT_ID = 1


def make_sub(search_id, chat_id, name="speediance", url="https://example.com",
             min_price=None, max_price=None, exclude_keywords=None, condition_filter=None):
    return UserSearch(
        search_id=search_id, chat_id=chat_id, name=name, url=url,
        min_price=min_price, max_price=max_price,
        exclude_keywords=exclude_keywords, condition_filter=condition_filter,
        paused=False,
    )


class FakeNotifier:
    def __init__(self, fail_for_chat_id=None):
        self.new_listings_calls = []  # (chat_id, search_name, listings)
        self.alerts = []  # (chat_id, text)
        self._fail_for_chat_id = fail_for_chat_id

    def send_new_listings(self, chat_id, search_name, listings):
        self.new_listings_calls.append((chat_id, search_name, listings))
        if self._fail_for_chat_id == chat_id:
            raise RuntimeError("Telegram send failed after retry: HTTPError")

    def send_alert(self, chat_id, text):
        self.alerts.append((chat_id, text))


@pytest.mark.asyncio
async def test_first_cycle_seeds_without_notifying(tmp_path):
    seen_store = SeenStore(str(tmp_path / "t.sqlite3"))
    notifier = FakeNotifier()
    state = SearchState()
    sub = make_sub(1, 111)

    async def fetch_html(url):
        return NORMAL_HTML

    await run_cycle_for_url("https://example.com", [sub], state, fetch_html, seen_store, notifier, "tunnel-info", ADMIN_CHAT_ID)

    assert notifier.new_listings_calls == []
    assert state.last_polled_at is not None


@pytest.mark.asyncio
async def test_second_cycle_notifies_only_the_new_listing_to_the_subscriber(tmp_path):
    seen_store = SeenStore(str(tmp_path / "t.sqlite3"))
    notifier = FakeNotifier()
    state = SearchState()
    sub = make_sub(1, 111)

    async def fetch_seed(url):
        return NORMAL_HTML

    await run_cycle_for_url("https://example.com", [sub], state, fetch_seed, seen_store, notifier, "tunnel-info", ADMIN_CHAT_ID)

    async def fetch_updated(url):
        return NORMAL_HTML_WITH_NEW_LISTING

    await run_cycle_for_url("https://example.com", [sub], state, fetch_updated, seen_store, notifier, "tunnel-info", ADMIN_CHAT_ID)

    assert len(notifier.new_listings_calls) == 1
    chat_id, search_name, new_listings = notifier.new_listings_calls[0]
    assert (chat_id, search_name) == (111, "speediance")
    assert [l.listing_id for l in new_listings] == ["2"]


@pytest.mark.asyncio
async def test_two_subscribers_of_the_same_url_each_get_their_own_seen_state(tmp_path):
    seen_store = SeenStore(str(tmp_path / "t.sqlite3"))
    notifier = FakeNotifier()
    state = SearchState()
    sub_a = make_sub(1, 111)

    async def fetch_seed(url):
        return NORMAL_HTML

    # sub_a seeds first, alone.
    await run_cycle_for_url("https://example.com", [sub_a], state, fetch_seed, seen_store, notifier, "tunnel-info", ADMIN_CHAT_ID)

    # sub_b joins later, for the same URL — it must ALSO see a clean first-run seed,
    # even though sub_a has already seen "1".
    sub_b = make_sub(2, 222)

    async def fetch_same(url):
        return NORMAL_HTML

    await run_cycle_for_url("https://example.com", [sub_a, sub_b], state, fetch_same, seen_store, notifier, "tunnel-info", ADMIN_CHAT_ID)

    assert notifier.new_listings_calls == []  # sub_b's first run, sub_a has nothing new


@pytest.mark.asyncio
async def test_price_filter_suppresses_notification_but_still_marks_seen(tmp_path):
    seen_store = SeenStore(str(tmp_path / "t.sqlite3"))
    notifier = FakeNotifier()
    state = SearchState()
    sub = make_sub(1, 111, min_price=1000.0, max_price=2000.0)  # excludes both S$10 and S$200 items

    async def fetch_seed(url):
        return NORMAL_HTML

    await run_cycle_for_url("https://example.com", [sub], state, fetch_seed, seen_store, notifier, "tunnel-info", ADMIN_CHAT_ID)

    async def fetch_updated(url):
        return NORMAL_HTML_WITH_NEW_LISTING

    await run_cycle_for_url("https://example.com", [sub], state, fetch_updated, seen_store, notifier, "tunnel-info", ADMIN_CHAT_ID)

    assert notifier.new_listings_calls == []  # filtered out
    # But marked seen regardless, so it won't be re-evaluated if the filter later changes.
    assert seen_store.get_new_ids(1, ["2"]) == []


@pytest.mark.asyncio
async def test_challenge_alert_goes_to_admin_not_subscribers(tmp_path):
    seen_store = SeenStore(str(tmp_path / "t.sqlite3"))
    notifier = FakeNotifier()
    state = SearchState()
    sub = make_sub(1, 111)  # a regular subscriber, not the admin

    async def fetch_challenge(url):
        return CHALLENGE_HTML

    await run_cycle_for_url("https://example.com", [sub], state, fetch_challenge, seen_store, notifier, "tunnel-info", ADMIN_CHAT_ID)

    assert len(notifier.alerts) == 1
    chat_id, text = notifier.alerts[0]
    assert chat_id == ADMIN_CHAT_ID
    assert "Cloudflare" in text


@pytest.mark.asyncio
async def test_challenge_page_pauses_and_sends_exactly_one_alert(tmp_path):
    seen_store = SeenStore(str(tmp_path / "t.sqlite3"))
    notifier = FakeNotifier()
    state = SearchState()
    sub = make_sub(1, 111)

    async def fetch_challenge(url):
        return CHALLENGE_HTML

    await run_cycle_for_url("https://example.com", [sub], state, fetch_challenge, seen_store, notifier, "tunnel-info", ADMIN_CHAT_ID)
    await run_cycle_for_url("https://example.com", [sub], state, fetch_challenge, seen_store, notifier, "tunnel-info", ADMIN_CHAT_ID)

    assert state.paused is True
    assert len(notifier.alerts) == 1


@pytest.mark.asyncio
async def test_challenge_clearing_resumes_normal_polling(tmp_path):
    seen_store = SeenStore(str(tmp_path / "t.sqlite3"))
    notifier = FakeNotifier()
    state = SearchState()
    sub = make_sub(1, 111)

    async def fetch_challenge(url):
        return CHALLENGE_HTML

    await run_cycle_for_url("https://example.com", [sub], state, fetch_challenge, seen_store, notifier, "tunnel-info", ADMIN_CHAT_ID)
    assert state.paused is True

    async def fetch_normal(url):
        return NORMAL_HTML

    await run_cycle_for_url("https://example.com", [sub], state, fetch_normal, seen_store, notifier, "tunnel-info", ADMIN_CHAT_ID)

    assert state.paused is False


@pytest.mark.asyncio
async def test_reminder_sent_once_after_30_minutes_still_paused(tmp_path):
    seen_store = SeenStore(str(tmp_path / "t.sqlite3"))
    notifier = FakeNotifier()
    state = SearchState()
    sub = make_sub(1, 111)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    async def fetch_challenge(url):
        return CHALLENGE_HTML

    await run_cycle_for_url("https://example.com", [sub], state, fetch_challenge, seen_store, notifier, "tunnel-info", ADMIN_CHAT_ID, now=lambda: start)
    await run_cycle_for_url("https://example.com", [sub], state, fetch_challenge, seen_store, notifier, "tunnel-info", ADMIN_CHAT_ID, now=lambda: start + timedelta(minutes=31))
    await run_cycle_for_url("https://example.com", [sub], state, fetch_challenge, seen_store, notifier, "tunnel-info", ADMIN_CHAT_ID, now=lambda: start + timedelta(minutes=32))

    assert len(notifier.alerts) == 2


@pytest.mark.asyncio
async def test_notify_failure_for_one_subscriber_does_not_lose_their_new_listing(tmp_path):
    seen_store = SeenStore(str(tmp_path / "t.sqlite3"))
    state = SearchState()
    sub = make_sub(1, 111)

    async def fetch_seed(url):
        return NORMAL_HTML

    await run_cycle_for_url("https://example.com", [sub], state, fetch_seed, seen_store, FakeNotifier(), "tunnel-info", ADMIN_CHAT_ID)

    async def fetch_updated(url):
        return NORMAL_HTML_WITH_NEW_LISTING

    failing = FakeNotifier(fail_for_chat_id=111)
    with pytest.raises(RuntimeError):
        await run_cycle_for_url("https://example.com", [sub], state, fetch_updated, seen_store, failing, "tunnel-info", ADMIN_CHAT_ID)

    working = FakeNotifier()
    await run_cycle_for_url("https://example.com", [sub], state, fetch_updated, seen_store, working, "tunnel-info", ADMIN_CHAT_ID)

    assert len(working.new_listings_calls) == 1
    assert [l.listing_id for l in working.new_listings_calls[0][2]] == ["2"]


@pytest.mark.asyncio
async def test_run_one_round_groups_by_url_and_isolates_failures(tmp_path):
    seen_store = SeenStore(str(tmp_path / "t.sqlite3"))
    notifier = FakeNotifier()
    subs = [
        make_sub(1, 111, name="broken", url="https://broken.example"),
        make_sub(2, 222, name="healthy", url="https://healthy.example"),
    ]
    states: dict = {}

    async def fetch_html(url):
        if url == "https://broken.example":
            raise RuntimeError("browser page crashed")
        return NORMAL_HTML

    succeeded, total = await run_one_round(subs, states, fetch_html, seen_store, notifier, "tunnel-info", ADMIN_CHAT_ID)
    assert (succeeded, total) == (1, 2)


@pytest.mark.asyncio
async def test_run_one_round_with_zero_subscriptions_is_not_a_failure(tmp_path):
    seen_store = SeenStore(str(tmp_path / "t.sqlite3"))
    notifier = FakeNotifier()

    async def fetch_html(url):
        raise AssertionError("should never be called")

    succeeded, total = await run_one_round([], {}, fetch_html, seen_store, notifier, "tunnel-info", ADMIN_CHAT_ID)

    assert (succeeded, total) == (0, 0)


@pytest.mark.asyncio
async def test_run_forever_picks_up_new_subscriptions_each_round(tmp_path):
    import asyncio

    seen_store = SeenStore(str(tmp_path / "t.sqlite3"))
    notifier = FakeNotifier()
    states: dict = {}
    calls = {"n": 0}
    subs_by_round = [[], [make_sub(1, 111)]]  # nothing tracked yet, then one search added

    def get_subscriptions():
        subs = subs_by_round[min(calls["n"], len(subs_by_round) - 1)]
        calls["n"] += 1
        return subs

    async def fetch_html(url):
        return NORMAL_HTML

    task = asyncio.ensure_future(
        run_forever(
            get_subscriptions, states, fetch_html, seen_store, notifier, "tunnel-info",
            ADMIN_CHAT_ID, poll_interval_seconds=0, poll_jitter_fraction=0,
        )
    )
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert calls["n"] >= 2  # ran at least the empty round and the one-subscription round


@pytest.mark.asyncio
async def test_all_urls_failing_alerts_admin_once_then_exits(tmp_path):
    seen_store = SeenStore(str(tmp_path / "t.sqlite3"))
    notifier = FakeNotifier()
    rounds = 0

    def get_subscriptions():
        return [make_sub(1, 111)]

    async def fetch_html(url):
        nonlocal rounds
        rounds += 1
        raise RuntimeError("browser is dead")

    with pytest.raises(BrowserLikelyDeadError):
        await run_forever(get_subscriptions, {}, fetch_html, seen_store, notifier, "tunnel-info", ADMIN_CHAT_ID, poll_interval_seconds=0, poll_jitter_fraction=0)

    assert rounds == MAX_CONSECUTIVE_ROUND_FAILURES
    assert len(notifier.alerts) == 1
    chat_id, text = notifier.alerts[0]
    assert chat_id == ADMIN_CHAT_ID
    assert "browser may be dead" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scheduler.py -v`
Expected: FAIL — `ImportError: cannot import name 'run_cycle_for_url'`

- [ ] **Step 3: Implement the rewrite in `src/carouauto/scheduler.py`**

Replace the file's contents in full:

```python
from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable

from .challenge import is_challenge_page
from .db import SeenStore
from .filters import passes_filters
from .parser import parse_listings
from .subscriptions import UserSearch

FetchHtmlFn = Callable[[str], Awaitable[str]]

CHALLENGE_REMINDER_AFTER_SECONDS = 30 * 60
MAX_CONSECUTIVE_ROUND_FAILURES = 5

logger = logging.getLogger("carouauto")


class BrowserLikelyDeadError(RuntimeError):
    """Raised to exit the process so systemd restarts it with a fresh browser."""


@dataclass
class SearchState:
    paused: bool = False
    paused_since: datetime | None = None
    reminder_sent: bool = False
    last_polled_at: datetime | None = None


async def run_cycle_for_url(
    url: str,
    subscribers: list[UserSearch],
    state: SearchState,
    fetch_html: FetchHtmlFn,
    seen_store: SeenStore,
    notifier,
    tunnel_instructions: str,
    admin_chat_id: int,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> None:
    html = await fetch_html(url)

    if is_challenge_page(html):
        if not state.paused:
            state.paused = True
            state.paused_since = now()
            state.reminder_sent = False
            notifier.send_alert(
                admin_chat_id,
                f"Cloudflare challenge is blocking '{url}'. Solve it manually:\n{tunnel_instructions}",
            )
        elif not state.reminder_sent and state.paused_since is not None:
            elapsed = (now() - state.paused_since).total_seconds()
            if elapsed > CHALLENGE_REMINDER_AFTER_SECONDS:
                state.reminder_sent = True
                notifier.send_alert(
                    admin_chat_id, f"Still waiting on a manual challenge solve for '{url}'."
                )
        logger.info("url '%s': challenge detected, paused", url)
        return

    if state.paused:
        state.paused = False
        state.paused_since = None
        state.reminder_sent = False

    listings = parse_listings(html)
    all_ids = [l.listing_id for l in listings]

    for sub in subscribers:
        new_ids = seen_store.get_new_ids(sub.search_id, all_ids)
        new_listings = [l for l in listings if l.listing_id in new_ids]
        filtered = [
            l
            for l in new_listings
            if passes_filters(l, sub.min_price, sub.max_price, sub.exclude_keywords, sub.condition_filter)
        ]
        if filtered:
            # If this raises, mark_seen below is skipped, so the same ids
            # are retried for this subscriber next cycle.
            notifier.send_new_listings(sub.chat_id, sub.name, filtered)
        seen_store.mark_seen(sub.search_id, all_ids)

    state.last_polled_at = now()
    logger.info("url '%s': polled, %d subscriber(s)", url, len(subscribers))


async def run_one_round(
    subscriptions: list[UserSearch],
    states: dict[str, SearchState],
    fetch_html: FetchHtmlFn,
    seen_store: SeenStore,
    notifier,
    tunnel_instructions: str,
    admin_chat_id: int,
) -> tuple[int, int]:
    """Poll every distinct URL once, fanning out to its subscribers.

    Returns (succeeded, total) URL counts. One URL failing must never
    stop the others from being polled.
    """
    by_url: dict[str, list[UserSearch]] = {}
    for sub in subscriptions:
        by_url.setdefault(sub.url, []).append(sub)

    succeeded = 0
    for url, subs in by_url.items():
        states.setdefault(url, SearchState())
        try:
            await run_cycle_for_url(
                url, subs, states[url], fetch_html, seen_store, notifier, tunnel_instructions, admin_chat_id
            )
            succeeded += 1
        except Exception as exc:
            logger.error("error polling '%s': %s", url, exc)
    return succeeded, len(by_url)


async def run_forever(
    get_subscriptions: Callable[[], list[UserSearch]],
    states: dict[str, SearchState],
    fetch_html: FetchHtmlFn,
    seen_store: SeenStore,
    notifier,
    tunnel_instructions: str,
    admin_chat_id: int,
    poll_interval_seconds: float,
    poll_jitter_fraction: float,
) -> None:
    consecutive_failed_rounds = 0
    while True:
        subscriptions = get_subscriptions()
        succeeded, total = await run_one_round(
            subscriptions, states, fetch_html, seen_store, notifier, tunnel_instructions, admin_chat_id
        )
        if total == 0 or succeeded > 0:
            consecutive_failed_rounds = 0
        else:
            consecutive_failed_rounds += 1
            if consecutive_failed_rounds >= MAX_CONSECUTIVE_ROUND_FAILURES:
                message = (
                    f"carouauto: all searches have failed for {consecutive_failed_rounds} "
                    "consecutive rounds — the browser may be dead. Restarting."
                )
                logger.error(message)
                try:
                    notifier.send_alert(admin_chat_id, message)
                except Exception as exc:
                    logger.error("failed to send browser-dead alert: %s", exc)
                raise BrowserLikelyDeadError(message)
        jitter = poll_interval_seconds * poll_jitter_fraction
        await asyncio.sleep(poll_interval_seconds + random.uniform(-jitter, jitter))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_scheduler.py -v`
Expected: PASS (14 tests)

- [ ] **Step 5: Commit**

```bash
git add src/carouauto/scheduler.py tests/test_scheduler.py
git commit -m "feat: rewrite scheduler for URL-dedup and per-subscriber fan-out"
```

---

## Task 12: `config.py` — drop static searches, add registration password

**Files:**
- Modify: `src/carouauto/config.py`
- Modify: `tests/test_config.py`

**Interfaces:**
- Produces: `AppConfig` **loses** the `searches: list[SearchConfig]`
  field (and `SearchConfig` itself is removed — searches now live in
  `SubscriptionStore`), and **gains** `registration_password: str`.
  `load_config` no longer requires or parses a `searches:` key in
  `config.yaml`, and now also requires `REGISTRATION_PASSWORD` in the
  environment (same env-first-then-`.env`-file precedence as the
  existing Telegram credentials).

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_config.py` in full:

```python
import pytest
from carouauto.config import load_config


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("REGISTRATION_PASSWORD", raising=False)


def write_env(tmp_path, token="abc123", chat_id="999", password="let-me-in"):
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"TELEGRAM_BOT_TOKEN={token}\nTELEGRAM_CHAT_ID={chat_id}\nREGISTRATION_PASSWORD={password}\n"
    )
    return env_file


def test_load_config_reads_settings_and_env(tmp_path):
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text("poll_interval_seconds: 60\npoll_jitter_fraction: 0.1\n")
    env_file = write_env(tmp_path)

    config = load_config(str(config_yaml), str(env_file))

    assert config.poll_interval_seconds == 60
    assert config.poll_jitter_fraction == 0.1
    assert config.telegram_bot_token == "abc123"
    assert config.telegram_chat_id == "999"
    assert config.registration_password == "let-me-in"


def test_load_config_defaults_poll_interval_to_five_minutes(tmp_path):
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text("")
    env_file = write_env(tmp_path)

    config = load_config(str(config_yaml), str(env_file))

    assert config.poll_interval_seconds == 300


def test_load_config_does_not_require_a_searches_list(tmp_path):
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text("db_path: custom.sqlite3\n")
    env_file = write_env(tmp_path)

    config = load_config(str(config_yaml), str(env_file))

    assert config.db_path == "custom.sqlite3"


def test_load_config_raises_when_missing_telegram_env(tmp_path):
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text("")
    env_file = tmp_path / ".env"
    env_file.write_text("REGISTRATION_PASSWORD=let-me-in\n")

    with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN"):
        load_config(str(config_yaml), str(env_file))


def test_load_config_raises_when_missing_registration_password(tmp_path):
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text("")
    env_file = tmp_path / ".env"
    env_file.write_text("TELEGRAM_BOT_TOKEN=abc123\nTELEGRAM_CHAT_ID=999\n")

    with pytest.raises(ValueError, match="REGISTRATION_PASSWORD"):
        load_config(str(config_yaml), str(env_file))


def test_process_environment_takes_precedence_over_env_file(tmp_path, monkeypatch):
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text("")
    env_file = write_env(tmp_path, token="from-file", chat_id="from-file", password="from-file")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "from-process")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "from-process")
    monkeypatch.setenv("REGISTRATION_PASSWORD", "from-process")

    config = load_config(str(config_yaml), str(env_file))

    assert config.telegram_bot_token == "from-process"
    assert config.registration_password == "from-process"


def test_process_environment_alone_is_sufficient(tmp_path, monkeypatch):
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text("")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "abc123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "999")
    monkeypatch.setenv("REGISTRATION_PASSWORD", "let-me-in")

    config = load_config(str(config_yaml), str(tmp_path / "does-not-exist.env"))

    assert config.telegram_bot_token == "abc123"
    assert config.registration_password == "let-me-in"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config.py -v`
Expected: FAIL — several tests error since `searches:` is still required
and `registration_password` doesn't exist yet.

- [ ] **Step 3: Implement the change in `src/carouauto/config.py`**

Replace the file's contents in full:

```python
from __future__ import annotations

import os
from dataclasses import dataclass

import yaml
from dotenv import dotenv_values


@dataclass
class AppConfig:
    poll_interval_seconds: float
    poll_jitter_fraction: float
    telegram_bot_token: str
    telegram_chat_id: str
    registration_password: str
    db_path: str
    user_data_dir: str


def load_config(config_path: str = "config.yaml", env_path: str = ".env") -> AppConfig:
    env_from_file = dotenv_values(env_path)

    with open(config_path) as f:
        raw = yaml.safe_load(f) or {}

    token = os.environ.get("TELEGRAM_BOT_TOKEN") or env_from_file.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID") or env_from_file.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in the environment")

    registration_password = os.environ.get("REGISTRATION_PASSWORD") or env_from_file.get(
        "REGISTRATION_PASSWORD"
    )
    if not registration_password:
        raise ValueError("REGISTRATION_PASSWORD must be set in the environment")

    return AppConfig(
        poll_interval_seconds=float(raw.get("poll_interval_seconds", 300)),
        poll_jitter_fraction=float(raw.get("poll_jitter_fraction", 0.2)),
        telegram_bot_token=token,
        telegram_chat_id=chat_id,
        registration_password=registration_password,
        db_path=raw.get("db_path", "carouauto.sqlite3"),
        user_data_dir=raw.get("user_data_dir", "browser-profile"),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_config.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/carouauto/config.py tests/test_config.py
git commit -m "feat: drop static searches list from config, require REGISTRATION_PASSWORD"
```

---

## Task 13: Rewrite `main.py` to wire both loops together

**Files:**
- Modify: `src/carouauto/main.py`

**Interfaces:**
- Consumes: `load_config` (Task 12), `SeenStore` (Task 3),
  `SubscriptionStore` (Task 2), `TelegramNotifier` (Task 5), `Poller`
  (existing, unchanged), `BotContext` (Task 6), `run_forever` (Task 11),
  `run_command_listener` (Task 10).
- Produces: `python -m carouauto.main` running both the poll loop and the
  command listener concurrently via `asyncio.gather` — the entrypoint
  systemd's `carouauto.service` invokes.

No automated test — this wires already-tested units together. Verified
manually in Step 3 against the real, live `speediance` search and a real
Telegram bot (this is the first point full end-to-end verification of
Task 10's listener is possible).

- [ ] **Step 1: Implement `src/carouauto/main.py`**

Replace the file's contents in full:

```python
from __future__ import annotations

import asyncio
import logging

from .commands import BotContext
from .config import load_config
from .db import SeenStore
from .notifier import TelegramNotifier
from .poller import Poller
from .scheduler import SearchState, run_forever
from .subscriptions import SubscriptionStore
from .telegram_listener import run_command_listener

TUNNEL_INSTRUCTIONS = (
    "1. From your machine: ssh -L 6080:localhost:6080 <user>@<vps-ip>\n"
    "2. Open http://localhost:6080/vnc.html in your browser\n"
    "3. Solve the challenge in the browser window shown\n"
)


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


async def main() -> None:
    configure_logging()
    config = load_config()
    admin_chat_id = int(config.telegram_chat_id)

    seen_store = SeenStore(config.db_path)
    subscriptions = SubscriptionStore(config.db_path)
    subscriptions.seed_admin(admin_chat_id)
    notifier = TelegramNotifier(config.telegram_bot_token)
    poller = Poller(user_data_dir=config.user_data_dir, headless=False)
    states: dict[str, SearchState] = {}

    ctx = BotContext(
        subscriptions=subscriptions,
        seen_store=seen_store,
        notifier=notifier,
        registration_password=config.registration_password,
        fetch_html=poller.fetch_search_html,
        states=states,
        db_path=config.db_path,
        poll_interval_seconds=config.poll_interval_seconds,
    )

    await poller.start()
    try:
        await asyncio.gather(
            run_forever(
                get_subscriptions=subscriptions.list_active_subscriptions,
                states=states,
                fetch_html=poller.fetch_search_html,
                seen_store=seen_store,
                notifier=notifier,
                tunnel_instructions=TUNNEL_INSTRUCTIONS,
                admin_chat_id=admin_chat_id,
                poll_interval_seconds=config.poll_interval_seconds,
                poll_jitter_fraction=config.poll_jitter_fraction,
            ),
            run_command_listener(config.telegram_bot_token, ctx),
        )
    finally:
        await poller.stop()
        seen_store.close()
        subscriptions.close()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Run the full test suite to confirm nothing else broke**

Run: `pytest -v`
Expected: PASS, all tests across every module.

- [ ] **Step 3: Manually verify end-to-end**

Update your local `.env` to add `REGISTRATION_PASSWORD=<pick one>`, then:

```bash
python -m carouauto.main
```

In Telegram, message your bot `/start`, then `/register <password>`,
then `/add speediance "https://www.carousell.sg/search/speediance?sort_by=3"`.
Confirm: the registration succeeds, the search is added, `/searches`
shows it, `/status` reports the poll interval and "not yet checked" (or
a checked time once a cycle has run), and `/list speediance` returns a
live snapshot. Leave it running for at least one real poll cycle and
confirm the process doesn't crash and logs a normal poll line.

- [ ] **Step 4: Commit**

```bash
git add src/carouauto/main.py
git commit -m "feat: wire poll loop and command listener together in main.py"
```

---

## Task 14: Deployment updates — config, secrets, and setup docs

**Files:**
- Modify: `config.yaml`
- Modify: `.env.example`
- Modify: `SETUP.md`

**Interfaces:**
- Consumes: nothing (config/docs only).
- Produces: an updated `config.yaml` with no `searches:` key, an updated
  `.env.example` documenting `REGISTRATION_PASSWORD`, and `SETUP.md`
  instructions reflecting the new registration-based flow instead of
  editing `config.yaml` by hand.

No automated test — this is deployment configuration and documentation.

- [ ] **Step 1: Update `config.yaml`**

Replace its contents (drop the `searches:` block entirely — searches are
now added via `/add` after deployment):

```yaml
poll_interval_seconds: 300
poll_jitter_fraction: 0.2
db_path: carouauto.sqlite3
user_data_dir: browser-profile
```

- [ ] **Step 2: Update `.env.example`**

```
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
REGISTRATION_PASSWORD=
```

- [ ] **Step 3: Update `SETUP.md`**

Replace the file's contents in full:

```markdown
# Setup

## 1. Create your Telegram bot and pick a registration password

Do this first — the service won't start without these.

1. Message **@BotFather** on Telegram, run `/newbot`, follow the prompts.
2. Copy the token into `.env` as `TELEGRAM_BOT_TOKEN`.
3. Pick a shared password for anyone you want to give access to, and put
   it in `.env` as `REGISTRATION_PASSWORD`. Anyone who knows it can
   register with the bot and track their own searches.
4. Get your own chat ID with `scripts/get_chat_id.py` (this makes you the
   admin — the only one who can `/revoke` others or run `/backup`), then
   paste it into `.env` as `TELEGRAM_CHAT_ID`.

   You can run it either way:

   - **From your own machine, before deploying** (simplest — the script only
     needs `httpx` and `python-dotenv`, no Playwright and no VPS):
     `python scripts/get_chat_id.py`
   - **On the VPS, after provisioning:**
     `sudo -u carouauto /opt/carouauto/.venv/bin/python scripts/get_chat_id.py`

## 2. Provision the VPS

- Spin up a 2GB RAM / 1-2 vCPU **Ubuntu 24.04 LTS or newer** VPS (e.g. Hetzner
  CX22). The provisioning script uses the distro's system Python, which must be
  3.11 or newer.
- Copy this repo to `/opt/carouauto` on the VPS (e.g. `git clone` or `scp -r`).
- Copy `.env.example` to `.env` and fill in `TELEGRAM_BOT_TOKEN`,
  `TELEGRAM_CHAT_ID`, and `REGISTRATION_PASSWORD` from step 1.
- Run: `sudo bash /opt/carouauto/deploy/provision.sh`

This installs everything and starts the support services (Xvfb, x11vnc, noVNC).
It enables `carouauto` but deliberately does **not** start it yet.

## 3. Start the monitor

With `.env` complete:

```bash
sudo systemctl start carouauto
```

## 4. Register and add your first search

Message your bot on Telegram:

```
/register <the password from step 1>
/add speediance https://www.carousell.sg/search/speediance?sort_by=3
```

Send `/help` for the full command list, or `/searches` to see what
you're tracking. Anyone else you give the password to does the same —
each person's searches and price/keyword/condition filters are their
own.

## 5. Check it's running

```bash
sudo systemctl status carouauto xvfb x11vnc novnc
sudo journalctl -u carouauto -f
```

You should see one log line per tracked search per poll cycle.

## 6. Solving a Cloudflare challenge manually

If Carousell shows a Cloudflare challenge that the automated poller
can't clear, the **admin** (the `TELEGRAM_CHAT_ID` from step 1) gets a
Telegram message with these instructions — regular users don't, since
only the admin has VPS access to act on it:

```bash
ssh -L 6080:localhost:6080 <your-user>@<vps-ip>
```

Then open `http://localhost:6080/vnc.html` in your own browser, solve the
challenge in the browser window shown, and polling resumes automatically
— no restart needed.

## 7. Backing up before a provider migration

As the admin, message the bot `/backup` — it sends the current database
(all registered users, their searches, and filters) back to you as a
Telegram file. On the new VPS, after cloning the repo and before starting
the service, drop that file in as `carouauto.sqlite3`.
```

- [ ] **Step 4: Commit**

```bash
git add config.yaml .env.example SETUP.md
git commit -m "docs: update config, .env.example, and SETUP.md for the interactive bot"
```

---

## Self-review notes

- **Spec coverage:** password-gated registration (Task 6), per-user
  private search lists with URL-deduplicated shared polling (Tasks 2,
  11), price/keyword/condition filters (Tasks 4, 11), `/pause`/`/resume`
  (Task 7), `/status` with the poll-interval caveat (Task 8), `/list` as
  an unfiltered on-demand snapshot (Task 8), admin `/revoke` (Task 9),
  admin `/backup` (Task 9), operational alerts routed to the admin only
  (Task 11), the poll loop re-reading subscriptions every round (Task
  11), and deployment docs (Task 14) are all covered. The spec's "future
  enhancements" (reliable bump detection, per-search poll intervals,
  bot-configurable global interval) are explicitly out of scope here, as
  the spec itself states.
- **Type consistency:** `UserSearch` (Task 2) fields are used identically
  in `filters.passes_filters`'s call site (Task 11), `commands.py`'s
  handlers (Tasks 6-9), and the scheduler tests (Task 11).
  `SeenStore.get_new_ids`/`mark_seen`/`delete_all_for_search` (Task 3)
  all take `search_id: int`, matching `UserSearch.search_id`'s type
  everywhere it's threaded through. `TelegramNotifier`'s per-call
  `chat_id: int` (Task 5) matches `UserSearch.chat_id: int` and the
  `admin_chat_id: int` threaded through the scheduler (Task 11) and
  `main.py` (Task 13). `BotContext` (Task 6) carries exactly the fields
  every handler in Tasks 6-9 and `main.py` (Task 13) actually reads.
- **No placeholders:** every step has runnable code; no "wire this up
  similarly" or "add appropriate handling" shortcuts. Tasks that modify a
  growing shared file (`commands.py` across Tasks 6-9) show the exact
  code being appended each time, not just a description of it.
