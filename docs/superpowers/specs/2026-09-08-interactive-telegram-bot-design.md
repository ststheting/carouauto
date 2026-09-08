# Interactive Multi-User Telegram Bot — Design

**Date:** 2026-09-08
**Status:** Approved for implementation planning

## Purpose

Turn the carouauto notifier from a one-way, single-recipient pusher with a
static `config.yaml` search list into an interactive, multi-user Telegram
bot: anyone who knows a shared password can register by DMing the bot, then
manage their own private list of tracked Carousell searches (with optional
price/keyword/condition filters) entirely through chat commands — no SSH,
no config file edits, no restart required for day-to-day use.

## Success criteria

- A new person can register with `/register <password>` and immediately
  start adding searches, with no server access.
- Two different users tracking the same Carousell URL each get their own
  independent "new since I subscribed" notification history, but the URL
  itself is only fetched from Carousell once per poll cycle regardless of
  how many users track it.
- `/add`, `/remove`, `/searches`, `/setprice`, `/setexclude`,
  `/setcondition`, `/pause`, `/resume`, `/status`, `/list`, `/revoke`,
  `/backup`, `/help` all work as documented below.
- Automatic notifications respect each user's price/keyword/condition
  filters and paused state; `/list`'s on-demand snapshot never does (it
  always shows everything currently on the page).
- An admin can revoke a user's access without needing to rotate the shared
  password (which would lock out everyone).
- An admin can retrieve a full backup of the SQLite database on demand via
  Telegram, with no SSH access needed, making a provider migration a
  ~15 minute job even with live user data at stake.
- The Telegram command listener and the Carousell poll loop run
  concurrently in one process; a crash in either lets the process exit so
  systemd's existing `Restart=on-failure` recovers it, consistent with the
  project's existing dead-browser-recovery philosophy — no new bespoke
  recovery mechanism.

## Non-goals

- Webhook-based Telegram integration (long-polling is sufficient and
  avoids needing a domain/TLS certificate).
- Rate-limiting or brute-force protection on `/register` (a hobby
  friend-group bot; the mitigation is "don't share the password widely").
- Scheduled/automatic backups (on-demand `/backup` only, per explicit
  decision).
- Group-chat notification targets (superseded by password-gated
  individual DM registration).
- Retroactively re-flagging previously-seen listings when a filter
  changes — filters only affect the comparison going forward.
- Filtering `/list`'s output — it is always an unfiltered, read-only
  snapshot.

## Architecture

```
┌──────────────────────┐        ┌───────────────────────────┐
│ Carousell poll loop   │        │ Telegram command listener  │
│ (existing, extended)  │        │ (new)                      │
│                       │        │                            │
│ fetch unique URLs ──▶ │        │ long-poll getUpdates ──▶   │
│ parse → per-subscriber│        │ parse command → dispatch  │
│ diff/filter/notify    │        │ to handler → reply         │
└──────────┬────────────┘        └─────────────┬──────────────┘
           │                                     │
           └──────────────┬──────────────────────┘
                           ▼
                  SQLite (users, user_searches,
                    seen_listings — now keyed
                    by search_id)
```

Both loops run as concurrent asyncio tasks in the same process
(`asyncio.gather`), sharing one `SeenStore`/DB connection and one
`TelegramNotifier`. An unhandled exception in either task is allowed to
propagate and exit the process — systemd's `Restart=on-failure` (already
configured) brings both back up together.

## Data model

```sql
CREATE TABLE users (
    chat_id INTEGER PRIMARY KEY,
    registered_at TEXT NOT NULL,
    is_admin BOOLEAN NOT NULL DEFAULT 0,
    revoked BOOLEAN NOT NULL DEFAULT 0
);

CREATE TABLE user_searches (
    search_id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL REFERENCES users(chat_id),
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    min_price REAL,
    max_price REAL,
    exclude_keywords TEXT,      -- comma-separated, NULL/empty = none
    condition_filter TEXT,      -- NULL = any condition
    paused BOOLEAN NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(chat_id, name)
);

-- seen_listings is re-keyed from search_name to search_id:
CREATE TABLE seen_listings (
    search_id INTEGER NOT NULL REFERENCES user_searches(search_id),
    listing_id TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY (search_id, listing_id)
);
```

**Migration note:** this replaces the currently-deployed single-tenant
schema (`seen_listings` keyed by `search_name`) with the multi-tenant one
above. Given the live deployment currently holds only a few poll cycles of
throwaway state (`speediance`, and a temporary `nintendo-switch` test
search due for removal), this is a clean cutover, not a data migration:
the implementation plan replaces `carouauto.sqlite3` on deploy, and the
admin re-adds their existing search(es) via `/add` once registered. No
migration-in-place logic is built for the old schema.

The shared registration password lives in `.env` as
`REGISTRATION_PASSWORD`, alongside the existing Telegram credentials — not
in the database. Whoever's chat ID is currently the deployment's
`TELEGRAM_CHAT_ID` is seeded as `is_admin = 1` on first startup.

`config.yaml` drops its `searches:` list entirely (superseded by
`user_searches`) but keeps `poll_interval_seconds`, `poll_jitter_fraction`,
`db_path`, and `user_data_dir`.

The `Listing` model gains a `condition: str` field, populated from the
same page text the parser already walks past today (the "Well used" /
"Brand new" text sibling to the price element).

## Command reference

| Command | Auth | Effect |
|---|---|---|
| `/start` | none | Welcome message + instructions to `/register` |
| `/register <password>` | none | Checks against `REGISTRATION_PASSWORD`; on success, inserts into `users` |
| `/add <name> <url> [min] [max]` | registered | Adds a `user_searches` row; `min`/`max` optional |
| `/remove <name>` | registered | Deletes that row (and its `seen_listings` rows) |
| `/searches` | registered | Lists your searches with their current filters/paused state |
| `/setprice <name> <min> <max>` | registered | Updates min/max price on an existing search |
| `/setexclude <name> <word1,word2,...>` | registered | Replaces the exclude-keyword list (`none` clears it) |
| `/setcondition <name> <condition>` | registered | Sets a condition filter (`any` clears it) |
| `/pause <name>` / `/resume <name>` | registered | Toggles the `paused` flag |
| `/status` | registered | Per-search: last successful poll time, paused-by-you or paused-by-Cloudflare-challenge |
| `/list <name>` | registered | Live, unfiltered snapshot of that search's current top listings (capped, e.g. 15); does not touch seen-state |
| `/revoke <chat_id>` | admin only | Sets `revoked = 1` for that user; they keep their data but stop receiving notifications and can no longer issue commands until re-registering |
| `/backup` | admin only | Sends the current SQLite file back as a Telegram document |
| `/help` | none | Lists commands |

Every command except `/start`, `/register`, and `/help` checks the
sender's `chat_id` against `users` (`revoked = 0`) before doing anything;
unregistered or revoked senders get a "you need to `/register` first"
reply. `/revoke` and `/backup` additionally require `is_admin = 1`.

## Polling & fan-out

Per cycle:

1. Compute the set of distinct URLs across all non-revoked users'
   non-paused `user_searches` rows.
2. Fetch and parse each unique URL once (existing `Poller`/`parser`
   pipeline, unchanged). Cloudflare challenge-pause state is tracked per
   URL (a `SearchState`-equivalent keyed by URL, not by user), consistent
   with today's single-search behavior — a challenge on a URL pauses
   notifications for every subscriber of that URL until solved.
3. For each `user_searches` row referencing that URL: diff against its own
   `seen_listings` (keyed by `search_id`), apply its price/keyword/
   condition filters to the newly-new listings, and notify only the
   surviving ones to that row's `chat_id`.

Filter semantics: price filter excludes a listing whose parsed numeric
price falls outside `[min_price, max_price]` (either bound may be unset);
keyword exclude is a case-insensitive substring match against the title;
condition filter requires an exact case-insensitive match against the
listing's condition text. A listing filtered out is still marked seen (it
will not be re-evaluated if the filter later changes) — filters are
forward-looking only, per the non-goals above.

## Error handling

- Command handler errors (malformed arguments, unknown search name, etc.)
  are caught per-command and replied to the user as a friendly message —
  never crash the listener loop.
- The Telegram long-poll loop's own transient network errors are retried
  in place (log and continue), consistent with the existing poll loop's
  per-cycle error isolation.
- An unhandled exception escaping either the poll loop or the command
  listener is allowed to propagate and exit the process, relying on
  systemd's `Restart=on-failure` — no new recovery mechanism is built
  beyond what already exists for the dead-browser case.

## Testing

- Command parser/dispatcher: pure-function unit tests (given message text,
  correct handler + parsed args), following the project's existing
  fixture-based style.
- Filter functions (price/keyword/condition): pure-function unit tests.
- `users`/`user_searches`/re-keyed `seen_listings` DB layer: real-sqlite
  tests in the same style as the existing `SeenStore` tests.
- URL-dedup-then-fan-out scheduler logic: extends the existing `run_cycle`
  test style to multiple subscribers per URL, using fakes for fetch/notify
  exactly as today.
- The actual Telegram `getUpdates` long-polling I/O is verified manually
  against a real bot, consistent with how the Playwright poller and
  Telegram sends are already handled — not mocked.

## Migration / backup

`/backup` (admin-only) sends the live SQLite file as a Telegram document.
Combined with the existing migration steps (git clone the repo onto the
new VPS, copy `.env`, run `deploy/provision.sh`), a provider migration is:
clone, copy `.env`, request `/backup` from the old deployment (or copy the
file directly if the old VPS is still reachable), drop it onto the new
VPS as `carouauto.sqlite3`, provision, start. `browser-profile/` remains
non-portable in any meaningful sense (a new VPS means a new IP, so
Cloudflare trust doesn't transfer regardless) and is not part of the
backup.
