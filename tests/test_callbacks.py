import pytest

from carouauto.commands import BotContext
from carouauto.callbacks import CallbackResult, dispatch_callback
from carouauto.db import SeenStore
from carouauto.subscriptions import SubscriptionStore


def make_ctx(tmp_path):
    return BotContext(
        subscriptions=SubscriptionStore(str(tmp_path / "t.sqlite3")),
        seen_store=SeenStore(str(tmp_path / "t.sqlite3")),
        notifier=None,
        registration_password="pw",
        fetch_html=None,
        states={},
        db_path=str(tmp_path / "t.sqlite3"),
        poll_interval_seconds=300,
    )


@pytest.mark.asyncio
async def test_ls_shows_the_searches_list(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    ctx.subscriptions.add_search(111, "speediance", "https://example.com/s")

    result = await dispatch_callback("ls", 111, ctx)

    assert "speediance" in result.text
    assert result.reply_markup["inline_keyboard"][0][0]["callback_data"].startswith("sp:")


@pytest.mark.asyncio
async def test_ls_for_an_inactive_user_asks_them_to_register(tmp_path):
    ctx = make_ctx(tmp_path)

    result = await dispatch_callback("ls", 111, ctx)

    assert "register" in result.text.lower()
    assert result.reply_markup is None


@pytest.mark.asyncio
async def test_sp_opens_the_panel_for_an_owned_search(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    search_id = ctx.subscriptions.add_search(111, "speediance", "https://example.com/s")

    result = await dispatch_callback(f"sp:{search_id}", 111, ctx)

    assert "speediance" in result.text
    flat = [b for row in result.reply_markup["inline_keyboard"] for b in row]
    assert any(b["callback_data"] == f"tg:{search_id}:pa" for b in flat)


@pytest.mark.asyncio
async def test_sp_rejects_a_search_owned_by_someone_else(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    ctx.subscriptions.register(222)
    search_id = ctx.subscriptions.add_search(222, "someone-elses", "https://example.com/s")

    result = await dispatch_callback(f"sp:{search_id}", 111, ctx)

    assert "no longer exists" in result.text.lower()
    assert result.reply_markup is None
    assert result.toast is not None


@pytest.mark.asyncio
async def test_sp_rejects_an_unknown_search_id(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)

    result = await dispatch_callback("sp:999999", 111, ctx)

    assert "no longer exists" in result.text.lower()


@pytest.mark.asyncio
async def test_tg_pa_toggles_pause_and_re_renders_the_panel(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    search_id = ctx.subscriptions.add_search(111, "speediance", "https://example.com/s")

    result = await dispatch_callback(f"tg:{search_id}:pa", 111, ctx)

    assert ctx.subscriptions.get_search_by_id(111, search_id).paused is True
    assert "resume" in result.text.lower() or "▶" in result.text
    flat = [b for row in result.reply_markup["inline_keyboard"] for b in row]
    assert any(b["callback_data"] == f"tg:{search_id}:pa" for b in flat)


@pytest.mark.asyncio
async def test_tg_hb_toggles_hide_bumped(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    search_id = ctx.subscriptions.add_search(111, "speediance", "https://example.com/s")
    assert ctx.subscriptions.get_search_by_id(111, search_id).hide_bumped is True  # default

    result = await dispatch_callback(f"tg:{search_id}:hb", 111, ctx)

    assert ctx.subscriptions.get_search_by_id(111, search_id).hide_bumped is False


@pytest.mark.asyncio
async def test_unknown_verb_returns_a_generic_error_without_raising(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)

    result = await dispatch_callback("nonsense:1:2:3", 111, ctx)

    assert isinstance(result, CallbackResult)
    assert result.reply_markup is None
