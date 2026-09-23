from datetime import datetime, timedelta, timezone

import httpx
import pytest

from carouauto.commands import BotContext, PendingInput
from carouauto.db import SeenStore
from carouauto.subscriptions import SubscriptionStore
from carouauto.telegram_listener import ADMIN_BOT_COMMANDS, DEFAULT_BOT_COMMANDS, handle_update, set_bot_commands


class FakeNotifier:
    def __init__(self):
        self.sent = []
        self.edits = []
        self.markup_edits = []
        self.answers = []

    def send_text(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text, reply_markup))

    def edit_message(self, chat_id, message_id, text, reply_markup):
        self.edits.append((chat_id, message_id, text, reply_markup))

    def edit_reply_markup(self, chat_id, message_id, reply_markup):
        self.markup_edits.append((chat_id, message_id, reply_markup))

    def answer_callback(self, callback_query_id, text=None):
        self.answers.append((callback_query_id, text))


def make_ctx(tmp_path):
    return BotContext(
        subscriptions=SubscriptionStore(str(tmp_path / "t.sqlite3")),
        seen_store=SeenStore(str(tmp_path / "t.sqlite3")),
        notifier=FakeNotifier(),
        registration_password="pw",
        fetch_html=None,
        states={},
        db_path=str(tmp_path / "t.sqlite3"),
        poll_interval_seconds=300,
    )


@pytest.mark.asyncio
async def test_handle_update_routes_a_plain_command(tmp_path):
    ctx = make_ctx(tmp_path)
    update = {"update_id": 1, "message": {"chat": {"id": 111}, "text": "/start"}}

    await handle_update(update, ctx)

    assert len(ctx.notifier.sent) == 1
    assert "register" in ctx.notifier.sent[0][1].lower()


@pytest.mark.asyncio
async def test_handle_update_routes_a_callback_query_and_always_answers(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    update = {
        "update_id": 2,
        "callback_query": {
            "id": "cbq1",
            "data": "ls",
            "message": {"chat": {"id": 111}, "message_id": 42},
        },
    }

    await handle_update(update, ctx)

    assert ctx.notifier.edits == [(111, 42, "You have no tracked searches yet. Use /add <name> to start one.", None)]
    assert ctx.notifier.answers == [("cbq1", None)]


@pytest.mark.asyncio
async def test_handle_update_on_a_card_verb_edits_only_the_keyboard_of_a_photo_message(tmp_path):
    # A real Telegram photo-card message has no "text" key — only "caption" —
    # and editMessageText cannot touch a photo message's caption or markup at
    # all. This exercises exactly that shape end to end through handle_update.
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    search_id = ctx.subscriptions.add_search(111, "speediance", "https://example.com/s")
    card_markup = {
        "inline_keyboard": [
            [{"text": "Open listing", "url": "https://example.com/p/1"}],
            [
                {"text": "🔇 Mute this search", "callback_data": f"cmt:{search_id}"},
                {"text": "🙈 Hide bumped", "callback_data": f"cbb:{search_id}"},
            ],
        ]
    }
    update = {
        "update_id": 5,
        "callback_query": {
            "id": "cbq2",
            "data": f"cmt:{search_id}",
            "message": {
                "chat": {"id": 111},
                "message_id": 77,
                "caption": "New listing for 'speediance':\n\nItem\nS$10",
                "reply_markup": card_markup,
            },
        },
    }

    await handle_update(update, ctx)

    assert ctx.notifier.edits == []  # editMessageText (which can't edit a photo) is never called
    assert len(ctx.notifier.markup_edits) == 1
    chat_id, message_id, reply_markup = ctx.notifier.markup_edits[0]
    assert chat_id == 111
    assert message_id == 77
    flat = [b for row in reply_markup["inline_keyboard"] for b in row]
    assert any(b.get("url") == "https://example.com/p/1" for b in flat)  # listing URL preserved
    assert ctx.subscriptions.get_search_by_id(111, search_id).paused is True
    assert "muted" in ctx.notifier.answers[0][1].lower()


@pytest.mark.asyncio
async def test_handle_update_ignores_a_message_with_no_text(tmp_path):
    ctx = make_ctx(tmp_path)
    update = {"update_id": 3, "message": {"chat": {"id": 111}}}

    await handle_update(update, ctx)  # must not raise

    assert ctx.notifier.sent == []


@pytest.mark.asyncio
async def test_a_reply_while_pending_setprice_updates_the_search(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    search_id = ctx.subscriptions.add_search(111, "speediance", "https://example.com/s")
    ctx.pending[111] = PendingInput(kind="setprice", search_id=search_id, created_at=datetime.now(timezone.utc))
    update = {"update_id": 1, "message": {"chat": {"id": 111}, "text": "100 500"}}

    await handle_update(update, ctx)

    found = ctx.subscriptions.get_search_by_id(111, search_id)
    assert (found.min_price, found.max_price) == (100.0, 500.0)
    assert 111 not in ctx.pending  # consumed
    assert ctx.notifier.sent  # a confirmation, re-rendered panel, was sent


@pytest.mark.asyncio
async def test_a_reply_of_none_clears_setprice(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    search_id = ctx.subscriptions.add_search(111, "speediance", "https://example.com/s")
    ctx.subscriptions.set_price_filter(111, "speediance", 10.0, 20.0)
    ctx.pending[111] = PendingInput(kind="setprice", search_id=search_id, created_at=datetime.now(timezone.utc))
    update = {"update_id": 1, "message": {"chat": {"id": 111}, "text": "none"}}

    await handle_update(update, ctx)

    found = ctx.subscriptions.get_search_by_id(111, search_id)
    assert (found.min_price, found.max_price) == (None, None)


@pytest.mark.asyncio
async def test_an_unparseable_price_reply_keeps_the_pending_state(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    search_id = ctx.subscriptions.add_search(111, "speediance", "https://example.com/s")
    ctx.pending[111] = PendingInput(kind="setprice", search_id=search_id, created_at=datetime.now(timezone.utc))
    update = {"update_id": 1, "message": {"chat": {"id": 111}, "text": "not a number"}}

    await handle_update(update, ctx)

    assert 111 in ctx.pending  # still waiting for a valid reply
    assert "number" in ctx.notifier.sent[-1][1].lower()


@pytest.mark.asyncio
async def test_a_reply_while_pending_setexclude_updates_the_search(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    search_id = ctx.subscriptions.add_search(111, "speediance", "https://example.com/s")
    ctx.pending[111] = PendingInput(kind="setexclude", search_id=search_id, created_at=datetime.now(timezone.utc))
    update = {"update_id": 1, "message": {"chat": {"id": 111}, "text": "case, box"}}

    await handle_update(update, ctx)

    found = ctx.subscriptions.get_search_by_id(111, search_id)
    assert found.exclude_keywords == "case, box"


@pytest.mark.asyncio
async def test_an_expired_pending_entry_is_ignored(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    search_id = ctx.subscriptions.add_search(111, "speediance", "https://example.com/s")
    old = datetime.now(timezone.utc) - timedelta(seconds=700)
    ctx.pending[111] = PendingInput(kind="setprice", search_id=search_id, created_at=old)
    update = {"update_id": 1, "message": {"chat": {"id": 111}, "text": "100 500"}}

    await handle_update(update, ctx)

    found = ctx.subscriptions.get_search_by_id(111, search_id)
    assert found.min_price is None  # not applied — the prompt had expired
    assert 111 not in ctx.pending  # expired entry is cleaned up


@pytest.mark.asyncio
async def test_a_command_while_pending_is_still_treated_as_a_command(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    search_id = ctx.subscriptions.add_search(111, "speediance", "https://example.com/s")
    ctx.pending[111] = PendingInput(kind="setprice", search_id=search_id, created_at=datetime.now(timezone.utc))
    update = {"update_id": 1, "message": {"chat": {"id": 111}, "text": "/status"}}

    await handle_update(update, ctx)

    assert 111 in ctx.pending  # untouched — /status isn't the awaited reply


@pytest.mark.asyncio
async def test_a_reply_while_pending_add_query_sends_the_add_flow_buttons(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    ctx.pending[111] = PendingInput(kind="add_query", search_id=None, created_at=datetime.now(timezone.utc))
    update = {"update_id": 1, "message": {"chat": {"id": 111}, "text": "speediance"}}

    await handle_update(update, ctx)

    assert ctx.pending[111].kind == "add_query"
    assert ctx.pending[111].query == "speediance"
    chat_id, text, reply_markup = ctx.notifier.sent[-1]
    flat = [b["callback_data"] for row in reply_markup["inline_keyboard"] for b in row]
    assert flat == ["afy", "afp", "afc"]


@pytest.mark.asyncio
async def test_a_reply_while_pending_add_price_finishes_the_add(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    ctx.pending[111] = PendingInput(kind="add_price", search_id=None, created_at=datetime.now(timezone.utc))
    ctx.pending[111].query = "speediance"
    update = {"update_id": 1, "message": {"chat": {"id": 111}, "text": "100 500"}}

    await handle_update(update, ctx)

    found = ctx.subscriptions.get_search(111, "speediance")
    assert (found.min_price, found.max_price) == (100.0, 500.0)
    assert 111 not in ctx.pending


def test_default_commands_exclude_admin_only_ones():
    names = {c["command"] for c in DEFAULT_BOT_COMMANDS}
    assert "revoke" not in names
    assert "backup" not in names
    assert "start" in names  # regular commands still present


def test_admin_commands_include_everything():
    names = {c["command"] for c in ADMIN_BOT_COMMANDS}
    assert "revoke" in names
    assert "backup" in names


@pytest.mark.asyncio
async def test_set_bot_commands_registers_default_and_admin_scoped_lists():
    posted = []

    class FakeAsyncClient:
        async def post(self, url, json=None, timeout=None):
            posted.append((url, json))
            return httpx.Response(200, request=httpx.Request("POST", url))

    await set_bot_commands(FakeAsyncClient(), "tok", admin_chat_id=42)

    assert len(posted) == 2
    default_call = next(p for p in posted if "scope" not in p[1])
    admin_call = next(p for p in posted if "scope" in p[1])
    assert default_call[1]["commands"] == DEFAULT_BOT_COMMANDS
    assert admin_call[1]["commands"] == ADMIN_BOT_COMMANDS
    assert admin_call[1]["scope"] == {"type": "chat", "chat_id": 42}
