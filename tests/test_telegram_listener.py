import pytest

from carouauto.commands import BotContext
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
