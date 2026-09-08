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


async def handle_add(args: list[str], chat_id: int, ctx: BotContext) -> str:
    if len(args) < 2:
        return "Usage: /add <name> <url> [min] [max]"
    name, url = args[0], args[1]
    min_price = max_price = None
    if len(args) >= 3:
        try:
            min_price = float(args[2])
        except ValueError:
            return "min price must be a number."
    if len(args) >= 4:
        try:
            max_price = float(args[3])
        except ValueError:
            return "max price must be a number."
    search_id = ctx.subscriptions.add_search(chat_id, name, url, min_price, max_price)
    if search_id is None:
        return f"You already have a search named '{name}'. Use /remove first or pick a different name."
    minutes = round(ctx.poll_interval_seconds / 60)
    return f"Added '{name}'. You'll be notified of new listings within ~{minutes} minutes of the next check."


async def handle_remove(args: list[str], chat_id: int, ctx: BotContext) -> str:
    if len(args) != 1:
        return "Usage: /remove <name>"
    search_id = ctx.subscriptions.remove_search(chat_id, args[0])
    if search_id is None:
        return f"No search named '{args[0]}'."
    ctx.seen_store.delete_all_for_search(search_id)
    return f"Removed '{args[0]}'."


async def handle_searches(args: list[str], chat_id: int, ctx: BotContext) -> str:
    subs = ctx.subscriptions.list_searches(chat_id)
    if not subs:
        return "You have no tracked searches yet. Use /add <name> <url> to start one."
    lines = []
    for sub in subs:
        parts = [sub.name]
        if sub.min_price is not None or sub.max_price is not None:
            lo = sub.min_price if sub.min_price is not None else "-"
            hi = sub.max_price if sub.max_price is not None else "-"
            parts.append(f"price {lo}-{hi}")
        if sub.exclude_keywords:
            parts.append(f"excluding: {sub.exclude_keywords}")
        if sub.condition_filter:
            parts.append(f"condition: {sub.condition_filter}")
        if sub.paused:
            parts.append("(paused)")
        lines.append(" | ".join(parts))
    return "\n".join(lines)


async def handle_setprice(args: list[str], chat_id: int, ctx: BotContext) -> str:
    if len(args) != 3:
        return "Usage: /setprice <name> <min> <max>"
    name, min_s, max_s = args
    try:
        min_price = float(min_s)
        max_price = float(max_s)
    except ValueError:
        return "min and max must be numbers."
    if not ctx.subscriptions.set_price_filter(chat_id, name, min_price, max_price):
        return f"No search named '{name}'."
    return f"Updated price filter for '{name}'."


async def handle_setexclude(args: list[str], chat_id: int, ctx: BotContext) -> str:
    if len(args) < 2:
        return "Usage: /setexclude <name> <word1,word2,...> (or 'none')"
    name = args[0]
    value = " ".join(args[1:]).strip()
    exclude = None if value.lower() == "none" else value
    if not ctx.subscriptions.set_exclude_keywords(chat_id, name, exclude):
        return f"No search named '{name}'."
    return f"Updated exclude list for '{name}'." if exclude else f"Cleared exclude list for '{name}'."


async def handle_setcondition(args: list[str], chat_id: int, ctx: BotContext) -> str:
    if len(args) < 2:
        return "Usage: /setcondition <name> <condition> (or 'any')"
    name = args[0]
    value = " ".join(args[1:]).strip()
    condition = None if value.lower() == "any" else value
    if not ctx.subscriptions.set_condition_filter(chat_id, name, condition):
        return f"No search named '{name}'."
    return (
        f"Set condition filter for '{name}' to '{condition}'."
        if condition
        else f"Cleared condition filter for '{name}'."
    )


async def handle_pause(args: list[str], chat_id: int, ctx: BotContext) -> str:
    if len(args) != 1:
        return "Usage: /pause <name>"
    if not ctx.subscriptions.set_paused(chat_id, args[0], True):
        return f"No search named '{args[0]}'."
    return f"Paused '{args[0]}'."


async def handle_resume(args: list[str], chat_id: int, ctx: BotContext) -> str:
    if len(args) != 1:
        return "Usage: /resume <name>"
    if not ctx.subscriptions.set_paused(chat_id, args[0], False):
        return f"No search named '{args[0]}'."
    return f"Resumed '{args[0]}'."
