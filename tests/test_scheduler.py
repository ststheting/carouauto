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
