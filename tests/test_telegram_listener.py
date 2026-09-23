from datetime import datetime, timedelta, timezone

import pytest

from carouauto.commands import BotContext, PendingInput
from carouauto.db import SeenStore
from carouauto.subscriptions import SubscriptionStore
from carouauto.telegram_listener import handle_update


class FakeNotifier:
    def __init__(self):
        self.sent = []
        self.edits = []
        self.answers = []

    def send_text(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text, reply_markup))

    def edit_message(self, chat_id, message_id, text, reply_markup):
        self.edits.append((chat_id, message_id, text, reply_markup))

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
