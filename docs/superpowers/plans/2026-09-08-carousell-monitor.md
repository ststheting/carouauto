# Carousell Listing Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python service that polls configured Carousell searches on
a schedule, detects genuinely new listings, and notifies the user via
Telegram — with a manual-intervention path for Cloudflare challenges.

**Architecture:** A small set of focused modules (config, db, parser,
challenge detector, notifier, poller, scheduler) composed in `main.py`.
The scheduler's per-search cycle logic (`run_cycle`) takes its I/O
(fetching HTML, sending notifications) as injected callables so it can be
unit tested with fakes, while the real Playwright browser and real
Telegram HTTP calls are exercised manually against the live site — per
the spec's testing section.

**Tech Stack:** Python 3.11+, Playwright (+ `playwright-stealth`),
BeautifulSoup4 + lxml, httpx, PyYAML, python-dotenv, pytest +
pytest-asyncio. Deployment: systemd, Xvfb, x11vnc, noVNC on a Ubuntu VPS.

**Spec:** [docs/superpowers/specs/2026-09-08-carousell-monitor-design.md](../specs/2026-09-08-carousell-monitor-design.md)

## Global Constraints

- Python >= 3.11.
- `listing_id` is the numeric suffix parsed from a listing card's
  `data-testid="listing-card-<id>"` attribute (confirmed stable against
  the live site; CSS class names are hashed/build-specific and must never
  be used as selectors).
- On the very first poll of a search, seed the seen-set silently — do not
  notify on any listings during that first poll (spec: no false-positive
  flood on first run).
- Every module that contains non-trivial logic (config parsing, diffing,
  HTML parsing, challenge detection, message formatting, cycle
  orchestration) gets real unit tests with real fixture data. Playwright
  browser control and live Telegram sends are verified manually per the
  spec — do not attempt to mock Playwright or the Telegram API
  extensively.
- One try/except boundary per search's cycle in the scheduler loop — one
  search failing must never stop others from polling.
- The real, running example throughout is the `speediance` search
  (`https://www.carousell.sg/search/speediance?sort_by=3`) — this is the
  user's actual use case and should be the one wired into `config.yaml`
  and used for end-to-end manual verification.

---

## Task 1: Project scaffolding, models, and config loader

**Files:**
- Create: `pyproject.toml`
- Create: `.env.example`
- Create: `config.yaml`
- Create: `src/carouauto/__init__.py`
- Create: `src/carouauto/models.py`
- Create: `src/carouauto/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `carouauto.models.Listing` — frozen dataclass with fields
  `listing_id: str`, `title: str`, `price: str`, `url: str`,
  `thumbnail_url: str`, `posted_text: str`.
- Produces: `carouauto.config.SearchConfig` — dataclass with fields
  `name: str`, `url: str`.
- Produces: `carouauto.config.AppConfig` — dataclass with fields
  `poll_interval_seconds: float`, `poll_jitter_fraction: float`,
  `searches: list[SearchConfig]`, `telegram_bot_token: str`,
  `telegram_chat_id: str`, `db_path: str`, `user_data_dir: str`.
- Produces: `carouauto.config.load_config(config_path: str = "config.yaml", env_path: str = ".env") -> AppConfig`.

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "carouauto"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "playwright>=1.47",
    "playwright-stealth>=1.0.6",
    "beautifulsoup4>=4.12",
    "lxml>=5.0",
    "httpx>=0.27",
    "pyyaml>=6.0",
    "python-dotenv>=1.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
]

[tool.hatch.build.targets.wheel]
packages = ["src/carouauto"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: Create `src/carouauto/__init__.py` (empty file) and install the project**

```bash
touch src/carouauto/__init__.py
pip install -e ".[dev]"
```

- [ ] **Step 3: Write the failing test for config loading**

Create `tests/test_config.py`:

```python
import pytest
from carouauto.config import load_config, SearchConfig


def test_load_config_reads_searches_and_env(tmp_path):
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text(
        "poll_interval_seconds: 60\n"
        "poll_jitter_fraction: 0.1\n"
        "searches:\n"
        "  - name: speediance\n"
        "    url: https://www.carousell.sg/search/speediance?sort_by=3\n"
    )
    env_file = tmp_path / ".env"
    env_file.write_text("TELEGRAM_BOT_TOKEN=abc123\nTELEGRAM_CHAT_ID=999\n")

    config = load_config(str(config_yaml), str(env_file))

    assert config.poll_interval_seconds == 60
    assert config.poll_jitter_fraction == 0.1
    assert config.searches == [
        SearchConfig(name="speediance", url="https://www.carousell.sg/search/speediance?sort_by=3")
    ]
    assert config.telegram_bot_token == "abc123"
    assert config.telegram_chat_id == "999"


def test_load_config_raises_when_no_searches(tmp_path):
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text("searches: []\n")
    env_file = tmp_path / ".env"
    env_file.write_text("TELEGRAM_BOT_TOKEN=abc123\nTELEGRAM_CHAT_ID=999\n")

    with pytest.raises(ValueError, match="at least one search"):
        load_config(str(config_yaml), str(env_file))


def test_load_config_raises_when_missing_telegram_env(tmp_path):
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text("searches:\n  - name: speediance\n    url: https://example.com\n")
    env_file = tmp_path / ".env"
    env_file.write_text("")

    with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN"):
        load_config(str(config_yaml), str(env_file))
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'carouauto.config'`

- [ ] **Step 5: Implement `src/carouauto/models.py`**

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
```

- [ ] **Step 6: Implement `src/carouauto/config.py`**

```python
from __future__ import annotations

import os
from dataclasses import dataclass

import yaml
from dotenv import load_dotenv


@dataclass
class SearchConfig:
    name: str
    url: str


@dataclass
class AppConfig:
    poll_interval_seconds: float
    poll_jitter_fraction: float
    searches: list[SearchConfig]
    telegram_bot_token: str
    telegram_chat_id: str
    db_path: str
    user_data_dir: str


def load_config(config_path: str = "config.yaml", env_path: str = ".env") -> AppConfig:
    load_dotenv(env_path)

    with open(config_path) as f:
        raw = yaml.safe_load(f) or {}

    searches = [SearchConfig(name=s["name"], url=s["url"]) for s in raw.get("searches") or []]
    if not searches:
        raise ValueError("config.yaml must define at least one search")

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in the environment")

    return AppConfig(
        poll_interval_seconds=float(raw.get("poll_interval_seconds", 90)),
        poll_jitter_fraction=float(raw.get("poll_jitter_fraction", 0.2)),
        searches=searches,
        telegram_bot_token=token,
        telegram_chat_id=chat_id,
        db_path=raw.get("db_path", "carouauto.sqlite3"),
        user_data_dir=raw.get("user_data_dir", "browser-profile"),
    )
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_config.py -v`
Expected: PASS (3 tests)

- [ ] **Step 8: Create `.env.example`**

```
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

- [ ] **Step 9: Create the real `config.yaml`, seeded with the speediance search**

```yaml
poll_interval_seconds: 90
poll_jitter_fraction: 0.2
db_path: carouauto.sqlite3
user_data_dir: browser-profile
searches:
  - name: speediance
    url: "https://www.carousell.sg/search/speediance?sort_by=3"
```

- [ ] **Step 10: Commit**

```bash
git add pyproject.toml .env.example config.yaml src/carouauto/__init__.py src/carouauto/models.py src/carouauto/config.py tests/test_config.py
git commit -m "feat: add project scaffolding, models, and config loader"
```

---

## Task 2: SQLite seen-listing store

**Files:**
- Create: `src/carouauto/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `carouauto.db.SeenStore(db_path: str)` with methods
  `diff_and_update(search_name: str, listing_ids: list[str]) -> list[str]`
  (returns newly-seen ids, `[]` on a search's first-ever call) and
  `close() -> None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_db.py`:

```python
from carouauto.db import SeenStore


def test_first_run_seeds_silently_and_reports_no_new_ids(tmp_path):
    store = SeenStore(str(tmp_path / "test.sqlite3"))

    new_ids = store.diff_and_update("speediance", ["111", "222"])

    assert new_ids == []


def test_second_run_reports_only_genuinely_new_ids(tmp_path):
    store = SeenStore(str(tmp_path / "test.sqlite3"))
    store.diff_and_update("speediance", ["111", "222"])

    new_ids = store.diff_and_update("speediance", ["111", "222", "333"])

    assert new_ids == ["333"]


def test_searches_are_tracked_independently(tmp_path):
    store = SeenStore(str(tmp_path / "test.sqlite3"))
    store.diff_and_update("speediance", ["111"])

    new_ids = store.diff_and_update("nintendo-switch", ["111"])

    assert new_ids == []  # first run for THIS search, seeded silently


def test_state_persists_across_store_instances(tmp_path):
    db_path = str(tmp_path / "test.sqlite3")
    store_one = SeenStore(db_path)
    store_one.diff_and_update("speediance", ["111"])
    store_one.close()

    store_two = SeenStore(db_path)
    new_ids = store_two.diff_and_update("speediance", ["111", "222"])

    assert new_ids == ["222"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'carouauto.db'`

- [ ] **Step 3: Implement `src/carouauto/db.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_db.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/carouauto/db.py tests/test_db.py
git commit -m "feat: add SQLite seen-listing store with per-search diffing"
```

---

## Task 3: Listing card parser

**Files:**
- Create: `src/carouauto/parser.py`
- Create: `tests/fixtures/search_results_normal.html`
- Test: `tests/test_parser.py`

**Interfaces:**
- Consumes: `carouauto.models.Listing` (Task 1).
- Produces: `carouauto.parser.parse_listings(html: str) -> list[Listing]`.

Real Carousell markup (confirmed via live inspection) keys each card on
`data-testid="listing-card-<numeric id>"`. CSS classes are hashed/build-
specific (e.g. `D_aJW M_aCT`) and must not be used as selectors. Within a
card: an `a[href^="/p/"]` holds the listing (with an `<img>` for
title/thumbnail and a `<p title="S$...">` for price), and an
`a[href^="/u/"]` holds the seller name and a relative posted-time string
(e.g. "18 hours ago").

- [ ] **Step 1: Create the fixture HTML**

Create `tests/fixtures/search_results_normal.html`:

```html
<div class="listing-grid">
  <div data-testid="listing-card-1460198499" class="card">
    <a href="/u/lilslilshop/?t-id=abc">
      <p data-testid="listing-card-text-seller-name">lilslilshop</p>
      <div><p>18 hours ago</p></div>
    </a>
    <a href="/p/speediance-gym-monster-smart-home-fitness-machine-1460198499/?t-id=abc&t-tap_index=0">
      <div>
        <img alt="Speediance Gym Monster Smart Home Fitness Machine" src="https://media.karousell.com/media/photos/products/speediance_thumb.jpg" />
      </div>
      <p>Speediance Gym Monster Smart Home Fitness Machine</p>
      <div><p title="S$599">S$599</p></div>
      <p>Well used</p>
    </a>
  </div>
  <div data-testid="listing-card-1451610227" class="card">
    <a href="/u/gyccc/?t-id=abc">
      <p data-testid="listing-card-text-seller-name">gyccc</p>
      <div><p>25 minutes ago</p></div>
    </a>
    <a href="/p/nintendo-switch-2-console-1451610227/?t-id=abc&t-tap_index=1">
      <div>
        <img alt="Nintendo Switch 2 Console" src="https://media.karousell.com/media/photos/products/nswitch_thumb.jpg" />
      </div>
      <p>Nintendo Switch 2 Console</p>
      <div><p title="S$590">S$590</p></div>
      <p>Brand new</p>
    </a>
  </div>
</div>
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_parser.py`:

```python
from pathlib import Path

from carouauto.models import Listing
from carouauto.parser import parse_listings

FIXTURES = Path(__file__).parent / "fixtures"


def test_parses_all_listing_cards():
    html = (FIXTURES / "search_results_normal.html").read_text()

    listings = parse_listings(html)

    assert len(listings) == 2


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
    )


def test_returns_empty_list_for_page_with_no_cards():
    assert parse_listings("<html><body>no results</body></html>") == []
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_parser.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'carouauto.parser'`

- [ ] **Step 4: Implement `src/carouauto/parser.py`**

```python
from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .models import Listing

BASE_URL = "https://www.carousell.sg"
_ID_RE = re.compile(r"listing-card-(\d+)")
_TIME_RE = re.compile(r"\d+\s+\w+\s+ago|Just now|Yesterday", re.IGNORECASE)


def parse_listings(html: str) -> list[Listing]:
    soup = BeautifulSoup(html, "lxml")
    listings: list[Listing] = []

    for card in soup.select('[data-testid^="listing-card-"]'):
        match = _ID_RE.search(card.get("data-testid", ""))
        if not match:
            continue
        listing_id = match.group(1)

        listing_link = card.select_one('a[href^="/p/"]')
        if listing_link is None:
            continue

        img = listing_link.select_one("img")
        title = img.get("alt", "").strip() if img else ""
        thumbnail_url = img.get("src", "") if img else ""
        url = urljoin(BASE_URL, listing_link["href"].split("?")[0])

        price_el = listing_link.select_one('p[title^="S$"]')
        price = price_el["title"] if price_el else ""

        posted_text = ""
        seller_link = card.select_one('a[href^="/u/"]')
        if seller_link is not None:
            time_match = _TIME_RE.search(seller_link.get_text(" ", strip=True))
            posted_text = time_match.group(0) if time_match else ""

        listings.append(
            Listing(
                listing_id=listing_id,
                title=title,
                price=price,
                url=url,
                thumbnail_url=thumbnail_url,
                posted_text=posted_text,
            )
        )

    return listings
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_parser.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add src/carouauto/parser.py tests/fixtures/search_results_normal.html tests/test_parser.py
git commit -m "feat: add listing card parser with fixture-based tests"
```

---

## Task 4: Cloudflare challenge detector

**Files:**
- Create: `src/carouauto/challenge.py`
- Test: `tests/test_challenge.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `carouauto.challenge.is_challenge_page(html: str) -> bool`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_challenge.py`:

```python
from carouauto.challenge import is_challenge_page


def test_detects_cloudflare_interstitial_title():
    html = "<html><head><title>Just a moment...</title></head><body></body></html>"
    assert is_challenge_page(html) is True


def test_detects_turnstile_widget():
    html = '<html><body><div class="cf-turnstile" data-sitekey="x"></div></body></html>'
    assert is_challenge_page(html) is True


def test_normal_search_results_page_is_not_a_challenge():
    html = '<html><body><div data-testid="listing-card-1">Item</div></body></html>'
    assert is_challenge_page(html) is False


def test_empty_page_is_not_a_challenge():
    assert is_challenge_page("") is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_challenge.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'carouauto.challenge'`

- [ ] **Step 3: Implement `src/carouauto/challenge.py`**

```python
_CHALLENGE_MARKERS = (
    "just a moment",
    "checking your browser",
    "cf-turnstile",
    "enable javascript and cookies to continue",
)


def is_challenge_page(html: str) -> bool:
    lowered = html.lower()
    return any(marker in lowered for marker in _CHALLENGE_MARKERS)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_challenge.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/carouauto/challenge.py tests/test_challenge.py
git commit -m "feat: add Cloudflare challenge page detector"
```

---

## Task 5: Telegram notifier

**Files:**
- Create: `src/carouauto/notifier.py`
- Test: `tests/test_notifier.py`

**Interfaces:**
- Consumes: `carouauto.models.Listing` (Task 1).
- Produces: `carouauto.notifier.format_message(listing: Listing) -> str`
  and `carouauto.notifier.TelegramNotifier(bot_token: str, chat_id: str)`
  with methods `send_new_listings(search_name: str, listings: list[Listing]) -> None`
  and `send_alert(text: str) -> None`.

Per the spec, only `format_message` (pure logic) gets a unit test here.
`TelegramNotifier`'s actual HTTP calls are verified manually against a
real bot in Task 8.

- [ ] **Step 1: Write the failing test**

Create `tests/test_notifier.py`:

```python
from carouauto.models import Listing
from carouauto.notifier import format_message


def test_format_message_includes_title_price_time_and_url():
    listing = Listing(
        listing_id="1460198499",
        title="Speediance Gym Monster Smart Home Fitness Machine",
        price="S$599",
        url="https://www.carousell.sg/p/speediance-gym-monster-1460198499/",
        thumbnail_url="https://media.karousell.com/thumb.jpg",
        posted_text="18 hours ago",
    )

    message = format_message(listing)

    assert "Speediance Gym Monster Smart Home Fitness Machine" in message
    assert "S$599" in message
    assert "18 hours ago" in message
    assert "https://www.carousell.sg/p/speediance-gym-monster-1460198499/" in message


def test_format_message_omits_blank_price_and_posted_text():
    listing = Listing(
        listing_id="1",
        title="Item",
        price="",
        url="https://www.carousell.sg/p/item-1/",
        thumbnail_url="",
        posted_text="",
    )

    message = format_message(listing)

    assert message == "Item\nhttps://www.carousell.sg/p/item-1/"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_notifier.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'carouauto.notifier'`

- [ ] **Step 3: Implement `src/carouauto/notifier.py`**

```python
from __future__ import annotations

import httpx

from .models import Listing

TELEGRAM_API_BASE = "https://api.telegram.org"


def format_message(listing: Listing) -> str:
    lines = [listing.title]
    if listing.price:
        lines.append(listing.price)
    if listing.posted_text:
        lines.append(listing.posted_text)
    lines.append(listing.url)
    return "\n".join(lines)


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str, client: httpx.Client | None = None):
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._client = client or httpx.Client(timeout=10.0)

    def send_new_listings(self, search_name: str, listings: list[Listing]) -> None:
        if not listings:
            return
        if len(listings) == 1:
            text = f"New listing for '{search_name}':\n\n{format_message(listings[0])}"
        else:
            bodies = "\n\n".join(format_message(l) for l in listings)
            text = f"{len(listings)} new listings for '{search_name}':\n\n{bodies}"
        self._send_text(text)

    def send_alert(self, text: str) -> None:
        self._send_text(text)

    def _send_text(self, text: str) -> None:
        url = f"{TELEGRAM_API_BASE}/bot{self._bot_token}/sendMessage"
        response = self._client.post(url, data={"chat_id": self._chat_id, "text": text})
        response.raise_for_status()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_notifier.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/carouauto/notifier.py tests/test_notifier.py
git commit -m "feat: add Telegram notifier with tested message formatting"
```

---

## Task 6: Playwright poller

**Files:**
- Create: `src/carouauto/poller.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `carouauto.poller.Poller(user_data_dir: str, headless: bool = False)`
  with async methods `start() -> None`, `stop() -> None`, and
  `fetch_search_html(url: str) -> str` (matches the `FetchHtmlFn` shape
  `Callable[[str], Awaitable[str]]` that Task 7's scheduler expects).

No unit test for this task — it requires a real browser and real network
access. Verified manually in Task 8 against the live `speediance` search.

- [ ] **Step 1: Install Playwright's browser binary**

```bash
playwright install chrome
```

- [ ] **Step 2: Implement `src/carouauto/poller.py`**

```python
from __future__ import annotations

import random

from playwright.async_api import BrowserContext, async_playwright
from playwright_stealth import stealth_async


class Poller:
    def __init__(self, user_data_dir: str, headless: bool = False):
        self._user_data_dir = user_data_dir
        self._headless = headless
        self._playwright = None
        self._context: BrowserContext | None = None

    async def start(self) -> None:
        self._playwright = await async_playwright().start()
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=self._user_data_dir,
            channel="chrome",
            headless=self._headless,
        )

    async def stop(self) -> None:
        if self._context is not None:
            await self._context.close()
        if self._playwright is not None:
            await self._playwright.stop()

    async def fetch_search_html(self, url: str) -> str:
        assert self._context is not None, "call start() before fetch_search_html()"
        page = await self._context.new_page()
        try:
            await stealth_async(page)
            await page.goto(url, wait_until="domcontentloaded")
            await page.wait_for_timeout(random.uniform(1000, 3000))
            return await page.content()
        finally:
            await page.close()
```

- [ ] **Step 3: Manually verify against the live speediance search**

Run this one-off script and confirm it prints listing-card markup
containing `data-testid="listing-card-`:

```bash
python -c "
import asyncio
from carouauto.poller import Poller

async def main():
    poller = Poller(user_data_dir='browser-profile', headless=False)
    await poller.start()
    html = await poller.fetch_search_html('https://www.carousell.sg/search/speediance?sort_by=3')
    print('listing-card' in html, html.count('listing-card-'))
    await poller.stop()

asyncio.run(main())
"
```

Expected: prints `True` and a count greater than 0.

- [ ] **Step 4: Commit**

```bash
git add src/carouauto/poller.py
git commit -m "feat: add Playwright poller with persistent browser context"
```

---

## Task 7: Cycle orchestration with pause/resume state machine

**Files:**
- Create: `src/carouauto/scheduler.py`
- Test: `tests/test_scheduler.py`

**Interfaces:**
- Consumes: `carouauto.parser.parse_listings` (Task 3),
  `carouauto.challenge.is_challenge_page` (Task 4),
  `carouauto.db.SeenStore` (Task 2), `carouauto.notifier.TelegramNotifier`
  (Task 5, used structurally — any object with matching
  `send_new_listings`/`send_alert` methods works, which is what the fake
  in the tests relies on).
- Produces: `carouauto.scheduler.SearchState` (dataclass:
  `paused: bool = False`, `paused_since: datetime | None = None`,
  `reminder_sent: bool = False`), `carouauto.scheduler.run_cycle(...)`,
  and `carouauto.scheduler.run_forever(...)` (used by Task 8's `main.py`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_scheduler.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest

from carouauto.db import SeenStore
from carouauto.scheduler import SearchState, run_cycle

NORMAL_HTML = """
<div data-testid="listing-card-1">
  <a href="/u/seller1/"><p data-testid="listing-card-text-seller-name">seller1</p><div><p>1 hour ago</p></div></a>
  <a href="/p/item-one-1/"><img alt="Item One" src="https://example.com/1.jpg"/><p>Item One</p><div><p title="S$10">S$10</p></div></a>
</div>
"""

NORMAL_HTML_WITH_NEW_LISTING = NORMAL_HTML + """
<div data-testid="listing-card-2">
  <a href="/u/seller2/"><p data-testid="listing-card-text-seller-name">seller2</p><div><p>2 minutes ago</p></div></a>
  <a href="/p/item-two-2/"><img alt="Item Two" src="https://example.com/2.jpg"/><p>Item Two</p><div><p title="S$20">S$20</p></div></a>
</div>
"""

CHALLENGE_HTML = "<html><head><title>Just a moment...</title></head></html>"


class FakeNotifier:
    def __init__(self):
        self.new_listings_calls = []
        self.alerts = []

    def send_new_listings(self, search_name, listings):
        self.new_listings_calls.append((search_name, listings))

    def send_alert(self, text):
        self.alerts.append(text)


@pytest.mark.asyncio
async def test_first_cycle_seeds_without_notifying(tmp_path):
    seen_store = SeenStore(str(tmp_path / "t.sqlite3"))
    notifier = FakeNotifier()
    state = SearchState()

    async def fetch_html(url):
        return NORMAL_HTML

    await run_cycle("speediance", "https://example.com", state, fetch_html, seen_store, notifier, "tunnel-info")

    assert notifier.new_listings_calls == []


@pytest.mark.asyncio
async def test_second_cycle_notifies_only_the_new_listing(tmp_path):
    seen_store = SeenStore(str(tmp_path / "t.sqlite3"))
    notifier = FakeNotifier()
    state = SearchState()

    async def fetch_seed(url):
        return NORMAL_HTML

    await run_cycle("speediance", "https://example.com", state, fetch_seed, seen_store, notifier, "tunnel-info")

    async def fetch_updated(url):
        return NORMAL_HTML_WITH_NEW_LISTING

    await run_cycle("speediance", "https://example.com", state, fetch_updated, seen_store, notifier, "tunnel-info")

    assert len(notifier.new_listings_calls) == 1
    search_name, new_listings = notifier.new_listings_calls[0]
    assert search_name == "speediance"
    assert [l.listing_id for l in new_listings] == ["2"]


@pytest.mark.asyncio
async def test_challenge_page_pauses_and_sends_exactly_one_alert(tmp_path):
    seen_store = SeenStore(str(tmp_path / "t.sqlite3"))
    notifier = FakeNotifier()
    state = SearchState()

    async def fetch_challenge(url):
        return CHALLENGE_HTML

    await run_cycle("speediance", "https://example.com", state, fetch_challenge, seen_store, notifier, "tunnel-info")
    await run_cycle("speediance", "https://example.com", state, fetch_challenge, seen_store, notifier, "tunnel-info")

    assert state.paused is True
    assert len(notifier.alerts) == 1


@pytest.mark.asyncio
async def test_challenge_clearing_resumes_normal_polling(tmp_path):
    seen_store = SeenStore(str(tmp_path / "t.sqlite3"))
    notifier = FakeNotifier()
    state = SearchState()

    async def fetch_challenge(url):
        return CHALLENGE_HTML

    await run_cycle("speediance", "https://example.com", state, fetch_challenge, seen_store, notifier, "tunnel-info")
    assert state.paused is True

    async def fetch_normal(url):
        return NORMAL_HTML

    await run_cycle("speediance", "https://example.com", state, fetch_normal, seen_store, notifier, "tunnel-info")

    assert state.paused is False


@pytest.mark.asyncio
async def test_reminder_sent_once_after_30_minutes_still_paused(tmp_path):
    seen_store = SeenStore(str(tmp_path / "t.sqlite3"))
    notifier = FakeNotifier()
    state = SearchState()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    async def fetch_challenge(url):
        return CHALLENGE_HTML

    await run_cycle(
        "speediance", "https://example.com", state, fetch_challenge, seen_store, notifier, "tunnel-info",
        now=lambda: start,
    )
    await run_cycle(
        "speediance", "https://example.com", state, fetch_challenge, seen_store, notifier, "tunnel-info",
        now=lambda: start + timedelta(minutes=31),
    )
    await run_cycle(
        "speediance", "https://example.com", state, fetch_challenge, seen_store, notifier, "tunnel-info",
        now=lambda: start + timedelta(minutes=32),
    )

    assert len(notifier.alerts) == 2  # initial alert + one reminder, not a third
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scheduler.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'carouauto.scheduler'`

- [ ] **Step 3: Implement `src/carouauto/scheduler.py`**

```python
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable

from .challenge import is_challenge_page
from .db import SeenStore
from .parser import parse_listings

FetchHtmlFn = Callable[[str], Awaitable[str]]

CHALLENGE_REMINDER_AFTER_SECONDS = 30 * 60


@dataclass
class SearchState:
    paused: bool = False
    paused_since: datetime | None = None
    reminder_sent: bool = False


async def run_cycle(
    search_name: str,
    search_url: str,
    state: SearchState,
    fetch_html: FetchHtmlFn,
    seen_store: SeenStore,
    notifier,
    tunnel_instructions: str,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> None:
    html = await fetch_html(search_url)

    if is_challenge_page(html):
        if not state.paused:
            state.paused = True
            state.paused_since = now()
            state.reminder_sent = False
            notifier.send_alert(
                f"Cloudflare challenge is blocking '{search_name}'. Solve it manually:\n{tunnel_instructions}"
            )
        elif not state.reminder_sent and state.paused_since is not None:
            elapsed = (now() - state.paused_since).total_seconds()
            if elapsed > CHALLENGE_REMINDER_AFTER_SECONDS:
                state.reminder_sent = True
                notifier.send_alert(f"Still waiting on a manual challenge solve for '{search_name}'.")
        return

    if state.paused:
        state.paused = False
        state.paused_since = None
        state.reminder_sent = False

    listings = parse_listings(html)
    new_ids = seen_store.diff_and_update(search_name, [l.listing_id for l in listings])
    new_listings = [l for l in listings if l.listing_id in new_ids]
    notifier.send_new_listings(search_name, new_listings)


async def run_forever(
    searches: list[tuple[str, str]],
    fetch_html: FetchHtmlFn,
    seen_store: SeenStore,
    notifier,
    tunnel_instructions: str,
    poll_interval_seconds: float,
    poll_jitter_fraction: float,
) -> None:
    states = {name: SearchState() for name, _ in searches}
    while True:
        for name, url in searches:
            try:
                await run_cycle(name, url, states[name], fetch_html, seen_store, notifier, tunnel_instructions)
            except Exception as exc:
                print(f"[carouauto] error polling '{name}': {exc}")
        jitter = poll_interval_seconds * poll_jitter_fraction
        await asyncio.sleep(poll_interval_seconds + random.uniform(-jitter, jitter))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_scheduler.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/carouauto/scheduler.py tests/test_scheduler.py
git commit -m "feat: add cycle orchestration with challenge pause/resume state machine"
```

---

## Task 8: Main entrypoint, chat-ID helper, and end-to-end verification

**Files:**
- Create: `src/carouauto/main.py`
- Create: `scripts/get_chat_id.py`

**Interfaces:**
- Consumes: `load_config` (Task 1), `SeenStore` (Task 2),
  `TelegramNotifier` (Task 5), `Poller` (Task 6), `run_forever` (Task 7).
- Produces: `python -m carouauto.main` as the process entrypoint.

No automated test — this task wires already-tested units together and is
verified by actually running it end-to-end against the live `speediance`
search and a real Telegram bot.

- [ ] **Step 1: Implement `src/carouauto/main.py`**

```python
from __future__ import annotations

import asyncio

from .config import load_config
from .db import SeenStore
from .notifier import TelegramNotifier
from .poller import Poller
from .scheduler import run_forever

TUNNEL_INSTRUCTIONS = (
    "1. From your machine: ssh -L 6080:localhost:6080 <user>@<vps-ip>\n"
    "2. Open http://localhost:6080/vnc.html in your browser\n"
    "3. Solve the challenge in the browser window shown\n"
)


async def main() -> None:
    config = load_config()
    seen_store = SeenStore(config.db_path)
    notifier = TelegramNotifier(config.telegram_bot_token, config.telegram_chat_id)
    poller = Poller(user_data_dir=config.user_data_dir, headless=False)

    await poller.start()
    try:
        await run_forever(
            searches=[(s.name, s.url) for s in config.searches],
            fetch_html=poller.fetch_search_html,
            seen_store=seen_store,
            notifier=notifier,
            tunnel_instructions=TUNNEL_INSTRUCTIONS,
            poll_interval_seconds=config.poll_interval_seconds,
            poll_jitter_fraction=config.poll_jitter_fraction,
        )
    finally:
        await poller.stop()
        seen_store.close()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Implement `scripts/get_chat_id.py`**

This is a one-time helper: after creating a bot via @BotFather and putting
its token in `.env`, run this, message the bot on Telegram, then press
Enter to capture the chat ID.

```python
import os
import sys

import httpx
from dotenv import load_dotenv


def main() -> None:
    load_dotenv()
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("Set TELEGRAM_BOT_TOKEN in .env first.")
        sys.exit(1)

    print("Send any message to your bot on Telegram now, then press Enter here.")
    input()

    response = httpx.get(f"https://api.telegram.org/bot{token}/getUpdates")
    response.raise_for_status()
    updates = response.json()["result"]
    if not updates:
        print("No messages found yet. Make sure you messaged the bot, then re-run this script.")
        sys.exit(1)

    chat_id = updates[-1]["message"]["chat"]["id"]
    print(f"Your chat ID is: {chat_id}")
    print("Add this to your .env as TELEGRAM_CHAT_ID=<that number>")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Create your Telegram bot and capture the chat ID**

In Telegram, message **@BotFather**, run `/newbot`, follow the prompts,
and copy the token it gives you into `.env` as `TELEGRAM_BOT_TOKEN`. Then:

```bash
cp .env.example .env
# edit .env, paste in TELEGRAM_BOT_TOKEN
python scripts/get_chat_id.py
# message your new bot on Telegram when prompted, then press Enter
# paste the printed chat ID into .env as TELEGRAM_CHAT_ID
```

- [ ] **Step 4: Run the app end-to-end against the real speediance search**

```bash
python -m carouauto.main
```

Expected: on the first poll cycle, no Telegram message (seeding). Leave
it running — when a genuinely new `speediance` listing appears on
Carousell, a Telegram message should arrive within roughly one poll
interval containing the title, price, posted time, and link. Confirm this
by watching the process logs and your Telegram chat for at least one full
poll interval.

- [ ] **Step 5: Commit**

```bash
git add src/carouauto/main.py scripts/get_chat_id.py
git commit -m "feat: add main entrypoint and Telegram chat-ID setup helper"
```

---

## Task 9: Deployment (systemd, Xvfb, x11vnc, noVNC) and setup docs

**Files:**
- Create: `deploy/carouauto.service`
- Create: `deploy/xvfb.service`
- Create: `deploy/x11vnc.service`
- Create: `deploy/novnc.service`
- Create: `deploy/provision.sh`
- Create: `SETUP.md`

**Interfaces:**
- Consumes: the installed `carouauto` package and `python -m carouauto.main`
  (Task 8) as the process the `carouauto.service` unit runs.

No automated test — this is infrastructure, verified manually on the
actual VPS.

- [ ] **Step 1: Create `deploy/xvfb.service`**

```ini
[Unit]
Description=Virtual display for headful Chrome

[Service]
Type=simple
ExecStart=/usr/bin/Xvfb :99 -screen 0 1280x800x24
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Create `deploy/x11vnc.service`**

```ini
[Unit]
Description=VNC server exposing Xvfb display :99
After=xvfb.service
Requires=xvfb.service

[Service]
Type=simple
Environment=DISPLAY=:99
ExecStart=/usr/bin/x11vnc -display :99 -forever -shared -rfbport 5900 -localhost -nopw
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 3: Create `deploy/novnc.service`**

```ini
[Unit]
Description=noVNC web client for x11vnc
After=x11vnc.service
Requires=x11vnc.service

[Service]
Type=simple
ExecStart=/opt/novnc/utils/novnc_proxy --vnc localhost:5900 --listen localhost:6080
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 4: Create `deploy/carouauto.service`**

```ini
[Unit]
Description=Carousell listing monitor
After=network-online.target xvfb.service
Requires=xvfb.service

[Service]
Type=simple
User=carouauto
WorkingDirectory=/opt/carouauto
EnvironmentFile=/opt/carouauto/.env
Environment=DISPLAY=:99
ExecStart=/opt/carouauto/.venv/bin/python -m carouauto.main
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 5: Create `deploy/provision.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail

apt-get update
apt-get install -y python3.11 python3.11-venv xvfb x11vnc git

if [ ! -d /opt/novnc ]; then
  git clone https://github.com/novnc/noVNC.git /opt/novnc
fi

id -u carouauto &>/dev/null || useradd -r -m -d /opt/carouauto -s /usr/sbin/nologin carouauto

sudo -u carouauto python3.11 -m venv /opt/carouauto/.venv
sudo -u carouauto /opt/carouauto/.venv/bin/pip install -e "/opt/carouauto[dev]"
sudo -u carouauto /opt/carouauto/.venv/bin/playwright install --with-deps chrome

cp /opt/carouauto/deploy/*.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now xvfb x11vnc novnc carouauto
```

- [ ] **Step 6: Create `SETUP.md`**

```markdown
# Setup

## 1. Provision the VPS

- Spin up a 2GB RAM / 1-2 vCPU Ubuntu LTS VPS (e.g. Hetzner CX22).
- Copy this repo to `/opt/carouauto` on the VPS (e.g. `git clone` or `scp -r`).
- Copy `.env.example` to `.env` and fill in `TELEGRAM_BOT_TOKEN` and
  `TELEGRAM_CHAT_ID` (see step 2).
- Edit `config.yaml` to list the searches you want tracked.
- Run: `sudo bash /opt/carouauto/deploy/provision.sh`

## 2. Create your Telegram bot

1. Message **@BotFather** on Telegram, run `/newbot`, follow the prompts.
2. Copy the token into `.env` as `TELEGRAM_BOT_TOKEN`.
3. Run `python scripts/get_chat_id.py`, message your bot when prompted,
   then paste the printed chat ID into `.env` as `TELEGRAM_CHAT_ID`.
4. Restart the service: `sudo systemctl restart carouauto`.

## 3. Check it's running

```bash
sudo systemctl status carouauto xvfb x11vnc novnc
sudo journalctl -u carouauto -f
```

## 4. Solving a Cloudflare challenge manually

If Carousell shows a Cloudflare challenge that the automated poller can't
clear, you'll get a Telegram message with these instructions:

```bash
ssh -L 6080:localhost:6080 <your-user>@<vps-ip>
```

Then open `http://localhost:6080/vnc.html` in your own browser, solve the
challenge in the browser window shown, and polling resumes automatically
— no restart needed.
```

- [ ] **Step 7: Commit**

```bash
git add deploy/ SETUP.md
git commit -m "feat: add systemd/Xvfb/noVNC deployment and setup docs"
```

---

## Self-review notes

- **Spec coverage:** architecture/components (Tasks 1-8), data flow and
  first-run seeding (Tasks 2, 7), Cloudflare strategy including stealth +
  persistent context (Task 6) and manual-intervention path (Tasks 7, 9),
  deployment/ops (Task 9), error handling — per-search try/except and
  notify-failure isolation (Task 7's `run_forever`, `_send_text` letting
  exceptions propagate to that same try/except rather than crashing the
  loop), testing (fixture-based unit tests in Tasks 2-5 and 7; manual
  verification called out explicitly in Tasks 6 and 8) are all covered.
- **Type consistency:** `Listing` fields (Task 1) are used identically in
  `parser.py` (Task 3), `notifier.py` (Task 5), and the scheduler tests
  (Task 7). `FetchHtmlFn`'s shape (`Callable[[str], Awaitable[str]]`)
  matches `Poller.fetch_search_html`'s actual signature (Task 6) and how
  `main.py` passes it to `run_forever` (Task 8).
- **No placeholders:** every step has runnable code; no "add error
  handling" or "similar to Task N" shortcuts.
