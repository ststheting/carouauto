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
