# Carousell Listing Monitor — Design

**Date:** 2026-09-08
**Status:** Approved for implementation planning

## Purpose

Monitor one or more Carousell search results for new listings and notify the
user via Telegram as soon as new listings appear, without needing to
manually refresh Carousell.

## Success criteria

- New listings matching a configured search show up as a Telegram message
  within roughly one poll interval (60-120s) of appearing on Carousell.
- No duplicate notifications for a listing already seen.
- No false-positive flood on first run for a newly added search.
- Runs unattended on a VPS for days/weeks at a time; degrades gracefully
  (rather than crashing or silently going dark) when Carousell blocks or
  challenges a request.
- When Cloudflare presents an interactive challenge the automated poller
  can't solve, the user can step in manually within a reasonable window
  without redeploying anything.

## Non-goals

- Real-time push/websocket integration with Carousell (polling only).
- Guaranteed, permanent Cloudflare bypass — this is a best-effort, monitor-
  and-recover posture, since anti-bot behavior can change over time.
- Multi-user support / multi-tenant deployment — single user, single
  Telegram chat.
- Paginating beyond the first page of "Recent"-sorted results (new listings
  always surface at the top of that sort order).

## Architecture

```
config.yaml (searches, intervals)
        │
        ▼
   Scheduler (asyncio loop)
        │
        ▼
   Poller (persistent Playwright browser context)
        │  extracts: listing_id, title, price, url, thumbnail_url, posted_text
        ▼
   Differ (SQLite: seen listing IDs per search)
        │  new listing IDs
        ▼
   Notifier (Telegram Bot API)
```

Four components, each independently testable:

- **Poller** — owns one persistent Playwright browser context (cookies/
  storage state persisted to disk) so Cloudflare clearance survives both
  poll cycles and process restarts. For each configured search, navigates
  to the search URL sorted by "Recent" and extracts listing cards.
- **Differ** — for each search, compares extracted listing IDs against a
  SQLite table of previously-seen IDs for that search (`search_id`,
  `listing_id`, `first_seen_at`, `last_seen_at`). New IDs are reported as
  "new listings"; all extracted IDs get upserted.
- **Notifier** — formats a Telegram message per new listing (title, price,
  thumbnail, link) and sends it via the Telegram Bot API to the user's
  chat ID. Batches into one message when many new listings appear in a
  single cycle (e.g. first poll of a newly added search after seeding).
- **Scheduler** — an asyncio loop: poll all configured searches, sleep
  (jittered), repeat. No external job queue needed at this scale.

## Data flow (per cycle)

1. Scheduler wakes every `poll_interval_seconds` (default 60-120s, jittered
   ±20% so the cadence isn't a perfect metronome).
2. For each search in config: Poller navigates the persistent browser
   context to that search's URL (sorted by Recent), waits for listing
   cards to render, extracts `(listing_id, title, price, url,
   thumbnail_url, posted_text)` for the first page only.
3. Differ queries SQLite for existing `(search_id, listing_id)` pairs,
   determines which extracted IDs are new, inserts/updates all extracted
   IDs (touching `last_seen_at`).
4. Any new IDs go to the Notifier as Telegram message(s).
5. On the very first poll of a brand-new search, the seen-set is seeded
   silently — nothing is reported as "new" until the second poll.

`listing_id` is parsed from the numeric suffix in each listing's URL
(e.g. `.../nintendo-switch-2-console-1451610227/` → `1451610227`),
confirmed stable via manual inspection of Carousell's search results.

## Cloudflare / anti-bot strategy

- **Playwright with stealth patches** (`playwright-stealth` or equivalent)
  and a real Chrome channel (`channel="chrome"`) rather than bundled
  Chromium, to reduce headless-automation tells.
- **One persistent browser context** reused across all cycles; storage
  state (cookies) saved to disk between process restarts so a Cloudflare
  clearance cookie isn't re-earned from scratch on every restart.
- **Human-ish cadence**: jittered poll interval, small randomized delay
  before extraction.
- **Single stable IP** (the VPS's own) — no rotating proxies, since
  rotating IPs look more suspicious for what's meant to look like one
  recurring visitor.
- **Detection + backoff**: if a poll gets a non-200, an empty result set,
  or matches a known challenge-page pattern (title/body text, Turnstile
  iframe presence), log it and back off that search for 1-2 cycles before
  retrying, rather than hammering.
- **Manual-intervention path for challenges the poller can't clear**:
  - Browser runs **headful** inside a virtual display (`Xvfb`) with
    `x11vnc` + **noVNC** (web-based VNC client) in front of it, so
    "connecting" is opening a URL in an ordinary browser tab.
  - noVNC bound to `localhost` only; reached via SSH tunnel
    (`ssh -L 6080:localhost:6080 user@vps`) — never exposed directly to
    the internet.
  - On challenge detection for a given search: pause polling for that
    search only (others continue), send a Telegram message with the
    tunnel command + noVNC URL, and watch in the background for the
    challenge to clear.
  - Once solved manually, the clearance cookie lands in the same
    persistent context the Poller already uses, so polling resumes
    automatically — no restart required.
  - If unsolved after ~30 minutes, send one follow-up reminder (not a
    repeating spam loop).

## Deployment & ops

- **VPS**: 2GB RAM / 1-2 vCPU (e.g. Hetzner CX22 or equivalent), Ubuntu
  LTS. Headful Chrome + Xvfb needs a bit more headroom than pure headless.
- **Process management**: systemd service for the poller app (auto-restart
  on crash), with `Xvfb` and `x11vnc`/`noVNC` as separate systemd units.
- **Secrets**: Telegram bot token + chat ID in a `.env` file (not
  committed), loaded via `python-dotenv`. Bot created by the user via
  @BotFather; chat ID captured interactively during setup.
- **Config**: `config.yaml` — list of searches (name, Carousell search URL,
  optional per-search poll interval override). Editable without touching
  code; picked up on service restart.
- **Persistence**: SQLite file + Playwright storage-state JSON on regular
  disk, both surviving restarts. Daily-rotated cron backup of the SQLite
  file.
- **Logging**: structured logs to stdout, captured by `journald`
  (`journalctl -u carouauto -f` for live tailing). No separate log infra.

## Error handling

- Network/timeout errors on a poll: log, skip cycle, retry next cycle —
  no crash-loop.
- Telegram send failures: log, retry once with backoff; never let a
  notify failure crash the poll loop.
- Each search's poll cycle wrapped in its own try/except so one search's
  failure doesn't take down the others.

## Testing

- Unit tests (`pytest`) for the Differ (pure logic against fixture
  listing data) and the listing-card parser (fixture HTML snapshots —
  this is what breaks first if Carousell changes markup).
- Poller/Notifier integration verified manually against the real site and
  a real Telegram bot during implementation, rather than mocked
  extensively — Playwright/Telegram mocking has low signal relative to
  cost here.

## Tech stack

Python: `playwright` (+ `playwright-stealth`), `python-telegram-bot` (or
raw Bot API via `httpx`), `sqlite3` (stdlib), `pyyaml`, `python-dotenv`,
`pytest`.
