from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx

from . import ui
from .callbacks import CARD_TEXT_UNCHANGED, dispatch_callback
from .commands import (
    PENDING_EXPIRY_SECONDS,
    BotContext,
    PendingInput,
    _build_carousell_search_url,
    _finish_add,
    _is_url,
    dispatch,
    parse_command,
)

logger = logging.getLogger("carouauto")

TELEGRAM_API_BASE = "https://api.telegram.org"
LONG_POLL_TIMEOUT_SECONDS = 30

# Registered with Telegram via setMyCommands so clients show a "/" menu with
# these commands and descriptions. This is a UX affordance only — it does not
# restrict what a user can send; dispatch() is still the single source of
# truth for what each command actually does and who can use it.
ADMIN_ONLY_COMMANDS = {"revoke", "backup"}

ADMIN_BOT_COMMANDS = [
    {"command": "start", "description": "Get started"},
    {"command": "register", "description": "Register: /register <password>"},
    {"command": "add", "description": "Track a search: /add <name> [query] [min] [max]"},
    {"command": "addurl", "description": "Track by exact URL: /addurl <name> <url> [min] [max]"},
    {"command": "remove", "description": "Stop tracking a search: /remove <name>"},
    {"command": "searches", "description": "List your tracked searches"},
    {"command": "setprice", "description": "Set a price filter: /setprice <name> <min> <max>"},
    {"command": "setexclude", "description": "Exclude keywords: /setexclude <name> <word,...>"},
    {"command": "setcondition", "description": "Filter by condition: /setcondition <name> <condition>"},
    {"command": "pause", "description": "Pause a search: /pause <name>"},
    {"command": "resume", "description": "Resume a search: /resume <name>"},
    {"command": "hidebumped", "description": "Hide bumped listings: /hidebumped <name>"},
    {"command": "showbumped", "description": "Show bumped listings again: /showbumped <name>"},
    {"command": "status", "description": "Check your searches' status"},
    {"command": "list", "description": "See current listings: /list <name>"},
    {"command": "revoke", "description": "Admin: revoke a user: /revoke <chat_id>"},
    {"command": "backup", "description": "Admin: get a database backup"},
    {"command": "help", "description": "Show all commands"},
]

DEFAULT_BOT_COMMANDS = [c for c in ADMIN_BOT_COMMANDS if c["command"] not in ADMIN_ONLY_COMMANDS]

# Kept for anything still importing the old name.
BOT_COMMANDS = ADMIN_BOT_COMMANDS


async def set_bot_commands(client: httpx.AsyncClient, bot_token: str, admin_chat_id: int) -> None:
    base = f"{TELEGRAM_API_BASE}/bot{bot_token}/setMyCommands"
    response = await client.post(base, json={"commands": DEFAULT_BOT_COMMANDS}, timeout=10)
    response.raise_for_status()
    response = await client.post(
        base,
        json={"commands": ADMIN_BOT_COMMANDS, "scope": {"type": "chat", "chat_id": admin_chat_id}},
        timeout=10,
    )
    response.raise_for_status()


async def get_updates(client: httpx.AsyncClient, bot_token: str, offset: int | None) -> list[dict]:
    params: dict[str, object] = {"timeout": LONG_POLL_TIMEOUT_SECONDS, "allowed_updates": ["message", "callback_query"]}
    if offset is not None:
        params["offset"] = offset
    response = await client.get(
        f"{TELEGRAM_API_BASE}/bot{bot_token}/getUpdates",
        params=params,
        timeout=LONG_POLL_TIMEOUT_SECONDS + 10,
    )
    response.raise_for_status()
    return response.json()["result"]


async def handle_update(update: dict, ctx: BotContext) -> None:
    message = update.get("message")
    if message and "text" in message:
        chat_id = message["chat"]["id"]
        text = message["text"]

        pending = ctx.pending.get(chat_id)
        if pending is not None and not text.startswith("/"):
            age = (datetime.now(timezone.utc) - pending.created_at).total_seconds()
            if age > PENDING_EXPIRY_SECONDS:
                del ctx.pending[chat_id]
            elif pending.kind == "add_query":
                pending.query = text.strip()
                ctx.notifier.send_text(chat_id, f"Track '{pending.query}'?", reply_markup=ui.add_flow_keyboard())
                return
            elif pending.kind == "add_price":
                await _apply_pending_add_price(pending, text, chat_id, ctx)
                return
            else:
                await _apply_pending_reply(pending, text, chat_id, ctx)
                return

        parsed = parse_command(text)
        if parsed is not None:
            command, args = parsed
            reply = await dispatch(command, args, chat_id, ctx)
            if reply:
                ctx.notifier.send_text(chat_id, reply)
        return

    callback = update.get("callback_query")
    if callback:
        data = callback.get("data", "")
        chat_id = callback["message"]["chat"]["id"]
        message_id = callback["message"]["message_id"]
        callback_query_id = callback["id"]
        result = await dispatch_callback(data, chat_id, ctx, callback["message"].get("reply_markup"))
        try:
            if result.text == CARD_TEXT_UNCHANGED:
                # A notification card's text/caption must stay exactly as it
                # was — editMessageReplyMarkup updates only the keyboard and
                # works the same whether the underlying message is text or a
                # photo (a photo message has no "text" field to substitute).
                ctx.notifier.edit_reply_markup(chat_id, message_id, result.reply_markup)
            else:
                ctx.notifier.edit_message(chat_id, message_id, result.text, result.reply_markup)
        except Exception as exc:
            logger.error("failed to edit message for callback '%s': %s", data, type(exc).__name__)
            # Spec: a failed edit (other than "not modified", which never
            # raises) falls back to sending a fresh message — the original
            # message may be too old to edit, or already deleted. Skip this
            # for CARD_TEXT_UNCHANGED: there's no real text there, just a
            # sentinel, so a fresh standalone message would be nonsense.
            if result.text != CARD_TEXT_UNCHANGED:
                try:
                    ctx.notifier.send_text(chat_id, result.text, result.reply_markup)
                except Exception as exc2:
                    logger.error(
                        "failed to send fallback message for callback '%s': %s", data, type(exc2).__name__
                    )
        # Answered before (and independent of) the force-reply send below, so
        # every callback query is answered — stopping the button's loading
        # spinner — even if that send fails.
        try:
            ctx.notifier.answer_callback(callback_query_id, result.toast)
        except Exception as exc:
            logger.error("failed to answer callback '%s': %s", data, type(exc).__name__)
        if result.force_reply_prompt:
            try:
                ctx.notifier.send_text(chat_id, result.force_reply_prompt, reply_markup={"force_reply": True})
            except Exception as exc:
                logger.error("failed to send force-reply prompt for callback '%s': %s", data, type(exc).__name__)


def _parse_two_optional_numbers(text: str) -> tuple[float | None, float | None] | str:
    if text.strip().lower() == "none":
        return None, None
    parts = text.split()
    if len(parts) != 2:
        return "invalid"
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        return "invalid"


async def _apply_pending_reply(pending: PendingInput, text: str, chat_id: int, ctx: BotContext) -> None:
    sub = ctx.subscriptions.get_search_by_id(chat_id, pending.search_id)
    if sub is None:
        del ctx.pending[chat_id]
        ctx.notifier.send_text(chat_id, "That search no longer exists.")
        return

    if pending.kind == "setprice":
        parsed = _parse_two_optional_numbers(text)
        if parsed == "invalid":
            ctx.notifier.send_text(chat_id, "min and max must both be numbers, or reply `none`. Try again:")
            return  # pending state kept — user gets another attempt
        min_price, max_price = parsed
        ctx.subscriptions.set_price_filter(chat_id, sub.name, min_price, max_price)
    elif pending.kind == "setexclude":
        value = None if text.strip().lower() == "none" else text.strip()
        ctx.subscriptions.set_exclude_keywords(chat_id, sub.name, value)

    del ctx.pending[chat_id]
    updated = ctx.subscriptions.get_search_by_id(chat_id, sub.search_id)
    ctx.notifier.send_text(chat_id, ui.search_panel_text(updated), reply_markup=ui.search_panel_keyboard(updated))


async def _apply_pending_add_price(pending: PendingInput, text: str, chat_id: int, ctx: BotContext) -> None:
    parsed = _parse_two_optional_numbers(text)
    if parsed == "invalid":
        ctx.notifier.send_text(chat_id, "min and max must both be numbers, or reply `none`. Try again:")
        return  # pending state kept
    min_price, max_price = parsed
    del ctx.pending[chat_id]
    url = pending.query if _is_url(pending.query) else _build_carousell_search_url(pending.query)
    reply = _finish_add(chat_id, pending.query, url, min_price, max_price, ctx)
    ctx.notifier.send_text(chat_id, reply)


async def run_command_listener(bot_token: str, ctx: BotContext, admin_chat_id: int) -> None:
    offset: int | None = None
    async with httpx.AsyncClient() as client:
        try:
            await set_bot_commands(client, bot_token, admin_chat_id)
        except httpx.HTTPError as exc:
            # Nice-to-have UX only — never let this block the bot from starting.
            logger.error("failed to set bot command menu: %s", type(exc).__name__)

        while True:
            try:
                updates = await get_updates(client, bot_token, offset)
            except httpx.HTTPError as exc:
                # httpx embeds the full request URL (which contains the bot
                # token) in its own exception message — never log str(exc).
                logger.error("error polling Telegram updates: %s", type(exc).__name__)
                await asyncio.sleep(5)
                continue
            except Exception as exc:
                logger.error("unexpected error in command listener: %s", type(exc).__name__)
                await asyncio.sleep(5)
                continue

            for update in updates:
                offset = update["update_id"] + 1
                try:
                    await handle_update(update, ctx)
                except Exception as exc:
                    # handle_update can call ctx.notifier.send_text(), which
                    # raises RuntimeError after exhausting its one retry.
                    # Without this guard that propagates out of the loop and
                    # (via asyncio.gather in main.py) kills the process —
                    # and since offset already advanced above, Telegram
                    # would never redeliver this same update to unstick it.
                    # Only the exception class name is logged — never
                    # str(exc): httpx's own message embeds the bot token.
                    logger.error(
                        "error handling update %s: %s", update.get("update_id"), type(exc).__name__
                    )
