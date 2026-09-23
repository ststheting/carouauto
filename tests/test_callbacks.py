from datetime import datetime, timezone

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


@pytest.mark.asyncio
async def test_cd_opens_the_condition_picker(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    search_id = ctx.subscriptions.add_search(111, "speediance", "https://example.com/s")

    result = await dispatch_callback(f"cd:{search_id}", 111, ctx)

    flat = [b for row in result.reply_markup["inline_keyboard"] for b in row]
    assert any(b["text"] == "Brand new" for b in flat)


@pytest.mark.asyncio
async def test_cds_sets_the_condition_and_returns_to_the_panel(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    search_id = ctx.subscriptions.add_search(111, "speediance", "https://example.com/s")

    result = await dispatch_callback(f"cds:{search_id}:0", 111, ctx)

    assert ctx.subscriptions.get_search_by_id(111, search_id).condition_filter == "Brand new"
    assert "speediance" in result.text  # back on the panel


@pytest.mark.asyncio
async def test_cds_any_clears_the_condition(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    search_id = ctx.subscriptions.add_search(111, "speediance", "https://example.com/s")
    ctx.subscriptions.set_condition_filter(111, "speediance", "Brand new")

    await dispatch_callback(f"cds:{search_id}:5", 111, ctx)

    assert ctx.subscriptions.get_search_by_id(111, search_id).condition_filter is None


@pytest.mark.asyncio
async def test_pp_prompts_for_price_and_records_pending_state(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    search_id = ctx.subscriptions.add_search(111, "speediance", "https://example.com/s")

    result = await dispatch_callback(f"pp:{search_id}", 111, ctx)

    assert result.force_reply_prompt is not None
    assert "min" in result.force_reply_prompt.lower()
    assert ctx.pending[111].kind == "setprice"
    assert ctx.pending[111].search_id == search_id


@pytest.mark.asyncio
async def test_px_prompts_for_exclude_words_and_records_pending_state(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    search_id = ctx.subscriptions.add_search(111, "speediance", "https://example.com/s")

    result = await dispatch_callback(f"px:{search_id}", 111, ctx)

    assert result.force_reply_prompt is not None
    assert ctx.pending[111].kind == "setexclude"
    assert ctx.pending[111].search_id == search_id


@pytest.mark.asyncio
async def test_pp_on_a_stale_search_does_not_set_pending_state(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)

    result = await dispatch_callback("pp:999999", 111, ctx)

    assert "no longer exists" in result.text.lower()
    assert 111 not in ctx.pending


@pytest.mark.asyncio
async def test_rm_asks_for_confirmation_without_deleting(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    search_id = ctx.subscriptions.add_search(111, "speediance", "https://example.com/s")

    result = await dispatch_callback(f"rm:{search_id}", 111, ctx)

    assert "sure" in result.text.lower()
    assert ctx.subscriptions.get_search_by_id(111, search_id) is not None


@pytest.mark.asyncio
async def test_rmy_deletes_the_search_and_its_seen_history(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    search_id = ctx.subscriptions.add_search(111, "speediance", "https://example.com/s")
    ctx.seen_store.mark_seen(search_id, ["1", "2"])

    result = await dispatch_callback(f"rmy:{search_id}", 111, ctx)

    assert ctx.subscriptions.get_search_by_id(111, search_id) is None
    assert ctx.seen_store.get_new_ids(search_id, ["1", "2"]) == []  # first-run semantics restored
    assert "removed" in result.text.lower()


@pytest.mark.asyncio
async def test_rmn_cancels_back_to_the_panel(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    search_id = ctx.subscriptions.add_search(111, "speediance", "https://example.com/s")

    result = await dispatch_callback(f"rmn:{search_id}", 111, ctx)

    assert ctx.subscriptions.get_search_by_id(111, search_id) is not None
    assert "speediance" in result.text
