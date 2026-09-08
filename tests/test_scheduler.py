from datetime import datetime, timedelta, timezone

import pytest

from carouauto.db import SeenStore
from carouauto.scheduler import (
    MAX_CONSECUTIVE_ROUND_FAILURES,
    BrowserLikelyDeadError,
    SearchState,
    run_cycle,
    run_forever,
    run_one_round,
)

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
    def __init__(self, fail_on_new_listings=False):
        self.new_listings_calls = []
        self.alerts = []
        self._fail_on_new_listings = fail_on_new_listings

    def send_new_listings(self, search_name, listings):
        self.new_listings_calls.append((search_name, listings))
        if self._fail_on_new_listings:
            raise RuntimeError("Telegram send failed after retry: HTTPError")

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


@pytest.mark.asyncio
async def test_notify_failure_does_not_mark_new_listing_as_seen(tmp_path):
    """Regression for C1: a failed Telegram send must not lose the new listing."""
    seen_store = SeenStore(str(tmp_path / "t.sqlite3"))
    state = SearchState()

    async def fetch_seed(url):
        return NORMAL_HTML

    async def fetch_updated(url):
        return NORMAL_HTML_WITH_NEW_LISTING

    # Seed silently.
    await run_cycle(
        "speediance", "https://example.com", state, fetch_seed, seen_store, FakeNotifier(), "tunnel-info"
    )

    # A new listing appears, but the notify blows up — the exception must
    # propagate (run_forever's job to catch), leaving nothing marked seen.
    failing = FakeNotifier(fail_on_new_listings=True)
    with pytest.raises(RuntimeError):
        await run_cycle(
            "speediance", "https://example.com", state, fetch_updated, seen_store, failing, "tunnel-info"
        )
    assert [l.listing_id for l in failing.new_listings_calls[0][1]] == ["2"]

    # Next cycle with a healthy notifier still reports it as new.
    working = FakeNotifier()
    await run_cycle(
        "speediance", "https://example.com", state, fetch_updated, seen_store, working, "tunnel-info"
    )

    assert len(working.new_listings_calls) == 1
    assert [l.listing_id for l in working.new_listings_calls[0][1]] == ["2"]

    # And once it succeeds, it is not re-reported.
    third = FakeNotifier()
    await run_cycle(
        "speediance", "https://example.com", state, fetch_updated, seen_store, third, "tunnel-info"
    )
    assert third.new_listings_calls == []


@pytest.mark.asyncio
async def test_run_one_round_isolates_search_failures(tmp_path):
    """The plan's global invariant: one search failing never stops the others."""
    seen_store = SeenStore(str(tmp_path / "t.sqlite3"))
    notifier = FakeNotifier()
    searches = [("broken", "https://broken.example"), ("healthy", "https://healthy.example")]
    states = {name: SearchState() for name, _ in searches}

    async def fetch_html(url):
        if url == "https://broken.example":
            raise RuntimeError("browser page crashed")
        return NORMAL_HTML

    # Round 1: broken search raises, healthy search seeds silently.
    succeeded = await run_one_round(searches, states, fetch_html, seen_store, notifier, "tunnel-info")
    assert succeeded == 1
    assert notifier.new_listings_calls == []

    # Round 2: the healthy search still completes a normal cycle and reports
    # its new listing, proving the broken search never blocked it.
    async def fetch_html_updated(url):
        if url == "https://broken.example":
            raise RuntimeError("browser page crashed")
        return NORMAL_HTML_WITH_NEW_LISTING

    succeeded = await run_one_round(
        searches, states, fetch_html_updated, seen_store, notifier, "tunnel-info"
    )

    assert succeeded == 1
    assert len(notifier.new_listings_calls) == 1
    search_name, new_listings = notifier.new_listings_calls[0]
    assert search_name == "healthy"
    assert [l.listing_id for l in new_listings] == ["2"]


@pytest.mark.asyncio
async def test_all_searches_failing_alerts_once_then_exits(tmp_path):
    """I4: a dead browser must alert and exit for systemd, not spin forever."""
    seen_store = SeenStore(str(tmp_path / "t.sqlite3"))
    notifier = FakeNotifier()
    rounds = 0

    async def fetch_html(url):
        nonlocal rounds
        rounds += 1
        raise RuntimeError("browser is dead")

    with pytest.raises(BrowserLikelyDeadError):
        await run_forever(
            searches=[("speediance", "https://example.com")],
            fetch_html=fetch_html,
            seen_store=seen_store,
            notifier=notifier,
            tunnel_instructions="tunnel-info",
            poll_interval_seconds=0,
            poll_jitter_fraction=0,
        )

    assert rounds == MAX_CONSECUTIVE_ROUND_FAILURES
    assert len(notifier.alerts) == 1
    assert "browser may be dead" in notifier.alerts[0]


@pytest.mark.asyncio
async def test_one_success_resets_the_failure_counter(tmp_path):
    """A search that recovers must clear the streak, so we never exit spuriously."""
    seen_store = SeenStore(str(tmp_path / "t.sqlite3"))
    notifier = FakeNotifier()
    searches = [("speediance", "https://example.com")]
    states = {name: SearchState() for name, _ in searches}

    async def failing(url):
        raise RuntimeError("transient")

    async def working(url):
        return NORMAL_HTML

    consecutive = 0
    # Fail just under the threshold, succeed once, then fail again.
    for fetch in [failing] * (MAX_CONSECUTIVE_ROUND_FAILURES - 1) + [working] + [failing] * 2:
        succeeded = await run_one_round(searches, states, fetch, seen_store, notifier, "tunnel-info")
        consecutive = 0 if succeeded > 0 else consecutive + 1

    assert consecutive == 2  # streak reset by the success, threshold never reached
    assert notifier.alerts == []
