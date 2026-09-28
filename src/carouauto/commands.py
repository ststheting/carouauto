from __future__ import annotations

import logging
import os
import shlex
import sqlite3
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable
from urllib.parse import quote

from .bump import FRESH_WINDOW_DAYS
from .challenge import is_challenge_page
from .db import SeenStore
from .notifier import TelegramNotifier, format_message
from .parser import parse_listings
from .scheduler import SearchState
from .subscriptions import SubscriptionStore, UserSearch
from . import ui

FetchHtmlFn = Callable[[str], Awaitable[str]]

logger = logging.getLogger("carouauto")


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
    pending: dict[int, PendingInput] = field(default_factory=dict)


PENDING_EXPIRY_SECONDS = 600


@dataclass
class PendingInput:
    kind: str  # "setprice" | "setexclude" | "add_query" | "add_price"
    search_id: int | None
    created_at: datetime
    query: str | None = None


# Mobile keyboards (notably iOS) auto-convert straight quotes to "smart"
# curly quotes, which shlex does not recognize as grouping delimiters —
# without this, `/add "nintendo switch"` typed on a phone silently splits
# into three words instead of grouping the quoted phrase into one argument.
_SMART_QUOTE_TRANSLATION = str.maketrans(
    {
        "“": '"',  # “
        "”": '"',  # ”
        "‘": "'",  # '
        "’": "'",  # '
    }
)


def parse_command(text: str) -> tuple[str, list[str]] | None:
    text = text.strip()
    if not text.startswith("/"):
        return None
    text = text.translate(_SMART_QUOTE_TRANSLATION)
    try:
        parts = shlex.split(text)
    except ValueError:
        parts = text.split()
    if not parts or len(parts[0]) <= 1:
        return None
    command = parts[0][1:].lower().split("@")[0]
    return command, parts[1:]


async def handle_start(args: list[str], chat_id: int, ctx: BotContext) -> str:
    if ctx.subscriptions.is_active(chat_id):
        ctx.notifier.send_text(
            chat_id,
            "Welcome back to carouauto. Use the buttons below the chat box, or /help for everything else.",
            reply_markup=ui.bottom_keyboard(),
        )
    else:
        ctx.notifier.send_text(
            chat_id,
            "Welcome to carouauto — a Carousell listing monitor.\n"
            "Send /register <password> to get started, then /help for what you can do.",
        )
    return ""


async def handle_register(args: list[str], chat_id: int, ctx: BotContext) -> str:
    if len(args) != 1:
        return "Usage: /register <password>"
    if args[0] != ctx.registration_password:
        return "Incorrect password."
    if ctx.subscriptions.register(chat_id):
        ctx.notifier.send_text(
            chat_id,
            "Registered! Use the buttons below the chat box, or /add <name> to track a search.",
            reply_markup=ui.bottom_keyboard(),
        )
        return ""
    return "You're already registered."


async def handle_help(args: list[str], chat_id: int, ctx: BotContext) -> str:
    minutes = round(ctx.poll_interval_seconds / 60)
    text = (
        "carouauto — Carousell listing monitor\n\n"
        f"Checks run roughly every {minutes} minutes — expect new listings "
        "a few minutes after they're posted, not instantly.\n\n"
        "/register <password> — get access\n"
        "/add <name> [query] [min] [max] — track a search; searches for <name> itself "
        "if you don't give a separate query\n"
        "/addurl <name> <url> [min] [max] — track a search using an exact Carousell URL\n"
        "/remove <name> — stop tracking a search\n"
        "/searches — list your tracked searches\n"
        "/setprice <name> <min> <max> — update a search's price filter\n"
        "/setexclude <name> <word1,word2,...> — exclude listings matching these words (or 'none')\n"
        "/setcondition <name> <condition> — only notify for this condition (or 'any')\n"
        "/pause <name> / /resume <name> — stop/resume notifications for a search\n"
        "/hidebumped <name> / /showbumped <name> — stop/resume notifying you about bumped "
        f"or otherwise stale (posted over {FRESH_WINDOW_DAYS} day ago) listings for a search "
        "— on by default for new searches\n"
        "/status — check your searches' last-poll status\n"
        "/list <name> — see what's currently on Carousell for a search right now\n"
        "Commands that take a <name> show your searches as buttons if you leave it out.\n"
        "/revoke <chat_id> — admin only, remove someone's access\n"
        "/backup — admin only, get a copy of the database\n"
        "/help — this message"
    )
    ctx.notifier.send_text(chat_id, text, reply_markup=ui.main_menu_keyboard())
    # A message carries one reply_markup, so the persistent bottom keyboard
    # needs its own message.
    if ctx.subscriptions.is_active(chat_id):
        ctx.notifier.send_text(chat_id, "Menu buttons are below the chat box.", reply_markup=ui.bottom_keyboard())
    return ""


def _build_carousell_search_url(query: str) -> str:
    return f"https://www.carousell.sg/search/{quote(query)}?sort_by=3"


def _is_url(text: str) -> bool:
    return text.startswith("http://") or text.startswith("https://")


def _parse_optional_price_args(args: list[str], start_index: int) -> tuple[float | None, float | None] | str:
    """Returns (min_price, max_price), or an error string if unparseable."""
    min_price = max_price = None
    if len(args) > start_index:
        try:
            min_price = float(args[start_index])
        except ValueError:
            return "min price must be a number."
    if len(args) > start_index + 1:
        try:
            max_price = float(args[start_index + 1])
        except ValueError:
            return "max price must be a number."
    return min_price, max_price


def _finish_add(
    chat_id: int, name: str, url: str, min_price: float | None, max_price: float | None, ctx: BotContext
) -> tuple[str, dict | None]:
    search_id = ctx.subscriptions.add_search(chat_id, name, url, min_price, max_price)
    if search_id is None:
        return f"You already have a search named '{name}'. Use /remove first or pick a different name.", None
    sub = ctx.subscriptions.get_search_by_id(chat_id, search_id)
    minutes = round(ctx.poll_interval_seconds / 60)
    header = (
        f"Added '{name}'. Checks run every ~{minutes} minutes. "
        "Bumped/stale listings are hidden by default — change it below."
    )
    panel = ui.search_panel_text(sub, ctx.states.get(sub.url))
    return f"{header}\n\n{panel}", ui.search_panel_keyboard(sub)


def _reply_with(chat_id: int, ctx: BotContext, text: str, markup: dict | None) -> str:
    if markup is None:
        return text
    ctx.notifier.send_text(chat_id, text, reply_markup=markup)
    return ""


def _send_picker(chat_id: int, ctx: BotContext, verb: str, missing_name: str | None) -> str:
    subs = ctx.subscriptions.list_searches(chat_id)
    if not subs:
        return "You have no tracked searches yet. Use /add <name> to start one."
    prompt = "Which search?" if missing_name is None else f"No search named '{missing_name}' — pick one:"
    ctx.notifier.send_text(chat_id, prompt, reply_markup=ui.search_picker_keyboard(subs, verb))
    return ""


def _start_prompt(chat_id: int, ctx: BotContext, sub: UserSearch, kind: str) -> str:
    ctx.pending[chat_id] = PendingInput(kind=kind, search_id=sub.search_id, created_at=datetime.now(timezone.utc))
    prompt = ui.price_prompt(sub.name) if kind == "setprice" else ui.exclude_prompt(sub.name)
    ctx.notifier.send_text(chat_id, prompt, reply_markup={"force_reply": True})
    return ""


async def handle_add(args: list[str], chat_id: int, ctx: BotContext) -> str:
    if len(args) < 1:
        ctx.pending[chat_id] = PendingInput(
            kind="add_query", search_id=None, created_at=datetime.now(timezone.utc)
        )
        ctx.notifier.send_text(
            chat_id, "What should I search for?", reply_markup={"force_reply": True}
        )
        return ""
    name = args[0]
    query = args[1] if len(args) >= 2 else args[0]
    prices = _parse_optional_price_args(args, 2)
    if isinstance(prices, str):
        return prices
    min_price, max_price = prices
    url = query if _is_url(query) else _build_carousell_search_url(query)
    text, markup = _finish_add(chat_id, name, url, min_price, max_price, ctx)
    return _reply_with(chat_id, ctx, text, markup)


async def handle_addurl(args: list[str], chat_id: int, ctx: BotContext) -> str:
    if len(args) < 2:
        return "Usage: /addurl <name> <url> [min] [max]"
    name, url = args[0], args[1]
    prices = _parse_optional_price_args(args, 2)
    if isinstance(prices, str):
        return prices
    min_price, max_price = prices
    text, markup = _finish_add(chat_id, name, url, min_price, max_price, ctx)
    return _reply_with(chat_id, ctx, text, markup)


async def handle_remove(args: list[str], chat_id: int, ctx: BotContext) -> str:
    if not args:
        return _send_picker(chat_id, ctx, "rm", None)
    name = " ".join(args)
    search_id = ctx.subscriptions.remove_search(chat_id, name)
    if search_id is None:
        return _send_picker(chat_id, ctx, "rm", name)
    ctx.seen_store.delete_all_for_search(search_id)
    return f"Removed '{name}'."


async def handle_searches(args: list[str], chat_id: int, ctx: BotContext) -> str:
    subs = ctx.subscriptions.list_searches(chat_id)
    if not subs:
        ctx.notifier.send_text(chat_id, "You have no tracked searches yet. Use /add <name> to start one.")
        return ""
    ctx.notifier.send_text(chat_id, "Your tracked searches:", reply_markup=ui.searches_list_keyboard(subs))
    return ""


async def handle_setprice(args: list[str], chat_id: int, ctx: BotContext) -> str:
    if not args:
        return _send_picker(chat_id, ctx, "pp", None)
    sub = ctx.subscriptions.get_search(chat_id, args[0])
    if sub is None:
        return _send_picker(chat_id, ctx, "pp", args[0])
    if len(args) != 3:
        return _start_prompt(chat_id, ctx, sub, "setprice")
    try:
        min_price = float(args[1])
        max_price = float(args[2])
    except ValueError:
        return "min and max must be numbers."
    ctx.subscriptions.set_price_filter(chat_id, sub.name, min_price, max_price)
    return f"Updated price filter for '{sub.name}'."


async def handle_setexclude(args: list[str], chat_id: int, ctx: BotContext) -> str:
    if not args:
        return _send_picker(chat_id, ctx, "px", None)
    sub = ctx.subscriptions.get_search(chat_id, args[0])
    if sub is None:
        return _send_picker(chat_id, ctx, "px", args[0])
    if len(args) == 1:
        return _start_prompt(chat_id, ctx, sub, "setexclude")
    value = " ".join(args[1:]).strip()
    exclude = None if value.lower() == "none" else value
    ctx.subscriptions.set_exclude_keywords(chat_id, sub.name, exclude)
    return f"Updated exclude list for '{sub.name}'." if exclude else f"Cleared exclude list for '{sub.name}'."


async def handle_setcondition(args: list[str], chat_id: int, ctx: BotContext) -> str:
    if not args:
        return _send_picker(chat_id, ctx, "cd", None)
    sub = ctx.subscriptions.get_search(chat_id, args[0])
    if sub is None:
        return _send_picker(chat_id, ctx, "cd", args[0])
    if len(args) == 1:
        ctx.notifier.send_text(chat_id, f"Set condition for '{sub.name}':", reply_markup=ui.condition_keyboard(sub))
        return ""
    value = " ".join(args[1:]).strip()
    condition = None if value.lower() == "any" else value
    ctx.subscriptions.set_condition_filter(chat_id, sub.name, condition)
    return (
        f"Set condition filter for '{sub.name}' to '{condition}'."
        if condition
        else f"Cleared condition filter for '{sub.name}'."
    )


async def handle_pause(args: list[str], chat_id: int, ctx: BotContext) -> str:
    if not args:
        return _send_picker(chat_id, ctx, "pa", None)
    name = " ".join(args)
    if not ctx.subscriptions.set_paused(chat_id, name, True):
        return _send_picker(chat_id, ctx, "pa", name)
    return f"Paused '{name}'."


async def handle_resume(args: list[str], chat_id: int, ctx: BotContext) -> str:
    if not args:
        return _send_picker(chat_id, ctx, "re", None)
    name = " ".join(args)
    if not ctx.subscriptions.set_paused(chat_id, name, False):
        return _send_picker(chat_id, ctx, "re", name)
    return f"Resumed '{name}'."


async def handle_hidebumped(args: list[str], chat_id: int, ctx: BotContext) -> str:
    if not args:
        return _send_picker(chat_id, ctx, "hb", None)
    name = " ".join(args)
    if not ctx.subscriptions.set_hide_bumped(chat_id, name, True):
        return _send_picker(chat_id, ctx, "hb", name)
    return f"'{name}' will no longer notify you about bumped (re-surfaced) listings."


async def handle_showbumped(args: list[str], chat_id: int, ctx: BotContext) -> str:
    if not args:
        return _send_picker(chat_id, ctx, "sb", None)
    name = " ".join(args)
    if not ctx.subscriptions.set_hide_bumped(chat_id, name, False):
        return _send_picker(chat_id, ctx, "sb", name)
    return f"'{name}' will notify you about bumped listings again."


LIST_CAP = 15


async def handle_status(args: list[str], chat_id: int, ctx: BotContext) -> str:
    subs = ctx.subscriptions.list_searches(chat_id)
    if not subs:
        return "You have no tracked searches. Use /add <name> to start one."
    minutes = round(ctx.poll_interval_seconds / 60)
    lines = [f"Poll interval: ~{minutes} minutes", ""]
    for sub in subs:
        lines.append(f"{sub.name}: {ui.status_line(sub, ctx.states.get(sub.url))}")
    return "\n".join(lines)


async def current_listings_text(sub: UserSearch, ctx: BotContext) -> str:
    html = await ctx.fetch_html(sub.url)
    if is_challenge_page(html):
        return "Carousell is showing a Cloudflare challenge right now — try again shortly."
    listings = parse_listings(html)
    if not listings:
        return f"No listings currently found for '{sub.name}'."
    lines = [f"Current listings for '{sub.name}' (showing up to {LIST_CAP}):", ""]
    for listing in listings[:LIST_CAP]:
        lines.append(format_message(listing))
        lines.append("")
    return "\n".join(lines).strip()


async def handle_list(args: list[str], chat_id: int, ctx: BotContext) -> str:
    if not args:
        return _send_picker(chat_id, ctx, "li", None)
    name = " ".join(args)
    sub = ctx.subscriptions.get_search(chat_id, name)
    if sub is None:
        return _send_picker(chat_id, ctx, "li", name)
    return await current_listings_text(sub, ctx)


async def handle_revoke(args: list[str], chat_id: int, ctx: BotContext) -> str:
    if len(args) != 1:
        return "Usage: /revoke <chat_id>"
    try:
        target = int(args[0])
    except ValueError:
        return "chat_id must be a number."
    if target == chat_id:
        return "You can't revoke your own admin access."
    if not ctx.subscriptions.revoke(target):
        return f"No user with chat_id {target}."
    return f"Revoked access for {target}."


async def handle_backup(args: list[str], chat_id: int, ctx: BotContext) -> str:
    fd, tmp_path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    try:
        source = sqlite3.connect(ctx.db_path)
        try:
            dest = sqlite3.connect(tmp_path)
            try:
                source.backup(dest)
            finally:
                dest.close()
        finally:
            source.close()
        ctx.notifier.send_document(chat_id, tmp_path, "carouauto_backup.sqlite3")
    finally:
        os.remove(tmp_path)
    return "📦 Backup sent above."


_PUBLIC_COMMANDS = {"start", "register", "help"}
_ADMIN_COMMANDS = {"revoke", "backup"}

_HANDLERS = {
    "start": handle_start,
    "register": handle_register,
    "help": handle_help,
    "add": handle_add,
    "addurl": handle_addurl,
    "remove": handle_remove,
    "searches": handle_searches,
    "setprice": handle_setprice,
    "setexclude": handle_setexclude,
    "setcondition": handle_setcondition,
    "pause": handle_pause,
    "resume": handle_resume,
    "hidebumped": handle_hidebumped,
    "showbumped": handle_showbumped,
    "status": handle_status,
    "list": handle_list,
    "revoke": handle_revoke,
    "backup": handle_backup,
}


async def dispatch(command: str, args: list[str], chat_id: int, ctx: BotContext) -> str:
    handler = _HANDLERS.get(command)
    if handler is None:
        return "Unknown command. Send /help for a list of commands."
    try:
        if command not in _PUBLIC_COMMANDS:
            if not ctx.subscriptions.is_active(chat_id):
                return "You need to /register first."
            if command in _ADMIN_COMMANDS and not ctx.subscriptions.is_admin(chat_id):
                return "This command is admin-only."
        return await handler(args, chat_id, ctx)
    except Exception as exc:
        logger.error("error handling /%s: %s", command, type(exc).__name__)
        return "Something went wrong handling that command."
