from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Awaitable, Callable

from .db import SeenStore
from .notifier import TelegramNotifier
from .scheduler import SearchState
from .subscriptions import SubscriptionStore

FetchHtmlFn = Callable[[str], Awaitable[str]]


@dataclass
class BotContext:
    subscriptions: SubscriptionStore
    seen_store: SeenStore
    notifier: TelegramNotifier
    registration_password: str
    fetch_html: FetchHtmlFn
    states: dict[str, SearchState]
    db_path: str
    poll_interval_seconds: float


def parse_command(text: str) -> tuple[str, list[str]] | None:
    text = text.strip()
    if not text.startswith("/"):
        return None
    try:
        parts = shlex.split(text)
    except ValueError:
        parts = text.split()
    if not parts or len(parts[0]) <= 1:
        return None
    command = parts[0][1:].lower().split("@")[0]
    return command, parts[1:]


async def handle_start(args: list[str], chat_id: int, ctx: BotContext) -> str:
    return (
        "Welcome to carouauto — a Carousell listing monitor.\n"
        "Send /register <password> to get started, then /help for what you can do."
    )


async def handle_register(args: list[str], chat_id: int, ctx: BotContext) -> str:
    if len(args) != 1:
        return "Usage: /register <password>"
    if args[0] != ctx.registration_password:
        return "Incorrect password."
    if ctx.subscriptions.register(chat_id):
        return "Registered! Try /add <name> <url> to track a search. See /help for all commands."
    return "You're already registered."


async def handle_help(args: list[str], chat_id: int, ctx: BotContext) -> str:
    minutes = round(ctx.poll_interval_seconds / 60)
    return (
        "carouauto — Carousell listing monitor\n\n"
        f"Checks run roughly every {minutes} minutes — expect new listings "
        "a few minutes after they're posted, not instantly.\n\n"
        "/register <password> — get access\n"
        "/add <name> <url> [min] [max] — track a search, optional price range\n"
        "/remove <name> — stop tracking a search\n"
        "/searches — list your tracked searches\n"
        "/setprice <name> <min> <max> — update a search's price filter\n"
        "/setexclude <name> <word1,word2,...> — exclude listings matching these words (or 'none')\n"
        "/setcondition <name> <condition> — only notify for this condition (or 'any')\n"
        "/pause <name> / /resume <name> — stop/resume notifications for a search\n"
        "/status — check your searches' last-poll status\n"
        "/list <name> — see what's currently on Carousell for a search right now\n"
        "/revoke <chat_id> — admin only, remove someone's access\n"
        "/backup — admin only, get a copy of the database\n"
        "/help — this message"
    )
