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
