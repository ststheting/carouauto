from datetime import datetime, timezone

import pytest

from carouauto.commands import BotContext, PendingInput
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
    assert "paused" in result.text.lower()
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


@pytest.mark.asyncio
async def test_afy_creates_the_search_from_the_pending_query(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    ctx.pending[111] = PendingInput(kind="add_query", search_id=None, created_at=datetime.now(timezone.utc))
    ctx.pending[111].query = "speediance"  # set by the listener before this callback fires, per Task 12 Step 3

    result = await dispatch_callback("afy", 111, ctx)

    found = ctx.subscriptions.get_search(111, "speediance")
    assert found is not None
    assert found.url == "https://www.carousell.sg/search/speediance?sort_by=3"
    assert "added" in result.text.lower()
    assert 111 not in ctx.pending


@pytest.mark.asyncio
async def test_afp_prompts_for_a_price_range(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    ctx.pending[111] = PendingInput(kind="add_query", search_id=None, created_at=datetime.now(timezone.utc))
    ctx.pending[111].query = "speediance"

    result = await dispatch_callback("afp", 111, ctx)

    assert result.force_reply_prompt is not None
    assert ctx.pending[111].kind == "add_price"
    assert ctx.pending[111].query == "speediance"  # carried forward


@pytest.mark.asyncio
async def test_afc_cancels_the_add_flow(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    ctx.pending[111] = PendingInput(kind="add_query", search_id=None, created_at=datetime.now(timezone.utc))
    ctx.pending[111].query = "speediance"

    result = await dispatch_callback("afc", 111, ctx)

    assert "cancelled" in result.text.lower()
    assert 111 not in ctx.pending
    assert ctx.subscriptions.get_search(111, "speediance") is None


@pytest.mark.asyncio
async def test_cmt_mutes_the_search_and_keeps_the_card_caption(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    search_id = ctx.subscriptions.add_search(111, "speediance", "https://example.com/s")

    result = await dispatch_callback(f"cmt:{search_id}", 111, ctx)

    assert ctx.subscriptions.get_search_by_id(111, search_id).paused is True
    assert "muted" in (result.toast or "").lower()


@pytest.mark.asyncio
async def test_cmt_on_a_stale_search_does_not_raise(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)

    result = await dispatch_callback("cmt:999999", 111, ctx)

    assert "no longer exists" in result.text.lower()


@pytest.mark.asyncio
async def test_cbb_toggles_hide_bumped_and_updates_the_card_keyboard(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    search_id = ctx.subscriptions.add_search(111, "speediance", "https://example.com/s")
    assert ctx.subscriptions.get_search_by_id(111, search_id).hide_bumped is True  # default

    result = await dispatch_callback(f"cbb:{search_id}", 111, ctx)

    assert ctx.subscriptions.get_search_by_id(111, search_id).hide_bumped is False
    flat = [b for row in result.reply_markup["inline_keyboard"] for b in row]
    bumped_button = next(b for b in flat if b.get("callback_data") == f"cbb:{search_id}")
    assert "hide" in bumped_button["text"].lower()


@pytest.mark.asyncio
async def test_mm_se_shows_the_searches_list(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    ctx.subscriptions.add_search(111, "speediance", "https://example.com/s")

    result = await dispatch_callback("mm:se", 111, ctx)

    assert "speediance" in result.text


@pytest.mark.asyncio
async def test_mm_ad_starts_the_guided_add_flow(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)

    result = await dispatch_callback("mm:ad", 111, ctx)

    assert result.force_reply_prompt is not None
    assert ctx.pending[111].kind == "add_query"


@pytest.mark.asyncio
async def test_mm_st_shows_status(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    ctx.subscriptions.add_search(111, "speediance", "https://example.com/s")

    result = await dispatch_callback("mm:st", 111, ctx)

    assert "speediance" in result.text
    assert "poll interval" in result.text.lower()


@pytest.mark.asyncio
async def test_mm_he_shows_help_text(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)

    result = await dispatch_callback("mm:he", 111, ctx)

    assert "/register" in result.text


class RecordingNotifier:
    def __init__(self):
        self.sent = []

    def send_text(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text, reply_markup))


LISTING_HTML = """
<div data-testid="listing-card-1">
  <a href="/u/seller1/"><p data-testid="listing-card-text-seller-name">seller1</p><div><p>2 minutes ago</p></div></a>
  <a href="/p/item-one-1/"><img alt="Item One" src="https://example.com/1.jpg"/><p>Item One</p><div><p title="S$10">S$10</p></div><p>Brand new</p></a>
</div>
"""


def _registered_ctx_with_search(tmp_path, name="speediance"):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    search_id = ctx.subscriptions.add_search(111, name, "https://example.com/s")
    return ctx, search_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "verb, field, start, expected, undo",
    [
        ("pa", "paused", False, True, "re"),
        ("re", "paused", True, False, "pa"),
        ("hb", "hide_bumped", False, True, "sb"),
        ("sb", "hide_bumped", True, False, "hb"),
    ],
)
async def test_state_verbs_set_explicitly_and_offer_undo(tmp_path, verb, field, start, expected, undo):
    ctx, search_id = _registered_ctx_with_search(tmp_path)
    if field == "paused":
        ctx.subscriptions.set_paused(111, "speediance", start)
    else:
        ctx.subscriptions.set_hide_bumped(111, "speediance", start)

    result = await dispatch_callback(f"{verb}:{search_id}", 111, ctx)

    assert getattr(ctx.subscriptions.get_search_by_id(111, search_id), field) is expected
    assert "speediance" in result.text
    assert result.reply_markup["inline_keyboard"] == [[{"text": "↩️ Undo", "callback_data": f"{undo}:{search_id}"}]]


@pytest.mark.asyncio
async def test_pa_is_idempotent_not_a_toggle(tmp_path):
    ctx, search_id = _registered_ctx_with_search(tmp_path)

    await dispatch_callback(f"pa:{search_id}", 111, ctx)
    await dispatch_callback(f"pa:{search_id}", 111, ctx)

    assert ctx.subscriptions.get_search_by_id(111, search_id).paused is True


@pytest.mark.asyncio
@pytest.mark.parametrize("verb", ["pa", "re", "hb", "sb", "li", "ms"])
async def test_new_verbs_reject_another_users_search(tmp_path, verb):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    ctx.subscriptions.register(222)
    search_id = ctx.subscriptions.add_search(222, "theirs", "https://example.com/s")

    result = await dispatch_callback(f"{verb}:{search_id}", 111, ctx)

    assert "no longer exists" in result.text.lower()
    assert result.followup is None
    assert ctx.subscriptions.get_search_by_id(222, search_id).paused is False


@pytest.mark.asyncio
async def test_li_answers_fast_and_sends_listings_in_the_followup(tmp_path):
    ctx, search_id = _registered_ctx_with_search(tmp_path)
    ctx.notifier = RecordingNotifier()

    async def fetch_html(url):
        return LISTING_HTML

    ctx.fetch_html = fetch_html

    result = await dispatch_callback(f"li:{search_id}", 111, ctx)

    assert "fetching" in result.text.lower()
    assert result.reply_markup["inline_keyboard"][0][0]["callback_data"] == f"sp:{search_id}"
    assert ctx.notifier.sent == []  # nothing sent until the followup runs
    await result.followup()
    assert "Item One" in ctx.notifier.sent[-1][1]


@pytest.mark.asyncio
async def test_li_followup_reports_a_fetch_failure(tmp_path):
    ctx, search_id = _registered_ctx_with_search(tmp_path)
    ctx.notifier = RecordingNotifier()

    async def fetch_html(url):
        raise RuntimeError("browser died")

    ctx.fetch_html = fetch_html

    result = await dispatch_callback(f"li:{search_id}", 111, ctx)
    await result.followup()  # must not raise

    assert "couldn't fetch listings for 'speediance'" in ctx.notifier.sent[-1][1].lower()


@pytest.mark.asyncio
async def test_ms_sends_the_panel_as_a_new_message(tmp_path):
    ctx, search_id = _registered_ctx_with_search(tmp_path)

    result = await dispatch_callback(f"ms:{search_id}", 111, ctx)

    assert result.send_as_new is True
    assert "speediance" in result.text
    flat = [b["callback_data"] for row in result.reply_markup["inline_keyboard"] for b in row]
    assert f"tg:{search_id}:pa" in flat


@pytest.mark.asyncio
async def test_cmt_toggles_mute_and_relabels_without_dropping_the_url(tmp_path):
    ctx, search_id = _registered_ctx_with_search(tmp_path)
    card = {
        "inline_keyboard": [
            [{"text": "Open listing", "url": "https://www.carousell.sg/p/x-1/"}],
            [
                {"text": "🔇 Mute this search", "callback_data": f"cmt:{search_id}"},
                {"text": "👁 Show bumped", "callback_data": f"cbb:{search_id}"},
            ],
            [{"text": "⚙️ Manage search", "callback_data": f"ms:{search_id}"}],
        ]
    }

    muted = await dispatch_callback(f"cmt:{search_id}", 111, ctx, card)
    assert ctx.subscriptions.get_search_by_id(111, search_id).paused is True
    assert muted.toast == "Muted 'speediance'."
    assert muted.reply_markup["inline_keyboard"][0][0]["url"] == "https://www.carousell.sg/p/x-1/"
    assert muted.reply_markup["inline_keyboard"][1][0]["text"] == "🔔 Unmute"
    assert card["inline_keyboard"][1][0]["text"] == "🔇 Mute this search"  # caller's dict untouched

    unmuted = await dispatch_callback(f"cmt:{search_id}", 111, ctx, muted.reply_markup)
    assert ctx.subscriptions.get_search_by_id(111, search_id).paused is False
    assert unmuted.toast == "Unmuted 'speediance'."
    assert unmuted.reply_markup["inline_keyboard"][1][0]["text"] == "🔇 Mute this search"


@pytest.mark.asyncio
async def test_cmt_without_current_markup_builds_a_keyboard_with_no_empty_url(tmp_path):
    ctx, search_id = _registered_ctx_with_search(tmp_path)

    result = await dispatch_callback(f"cmt:{search_id}", 111, ctx)

    flat = [b for row in result.reply_markup["inline_keyboard"] for b in row]
    assert all("url" not in b for b in flat)
