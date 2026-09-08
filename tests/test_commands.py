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
