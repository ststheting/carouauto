from __future__ import annotations

import asyncio
import logging

import httpx

from .commands import BotContext, dispatch, parse_command

logger = logging.getLogger("carouauto")

TELEGRAM_API_BASE = "https://api.telegram.org"
LONG_POLL_TIMEOUT_SECONDS = 30

# Registered with Telegram via setMyCommands so clients show a "/" menu with
# these commands and descriptions. This is a UX affordance only — it does not
# restrict what a user can send; dispatch() is still the single source of
# truth for what each command actually does and who can use it.
BOT_COMMANDS = [
    {"command": "start", "description": "Get started"},
    {"command": "register", "description": "Register: /register <password>"},
    {"command": "add", "description": "Track a search: /add <name> [query] [min] [max]"},
    {"command": "addurl", "description": "Track by exact URL: /addurl <name> <url> [min] [max]"},
    {"command": "remove", "description": "Stop tracking a search: /remove <name>"},
    {"command": "searches", "description": "List your tracked searches"},
    {"command": "setprice", "description": "Set a price filter: /setprice <name> <min> <max>"},
    {"command": "setexclude", "description": "Exclude keywords: /setexclude <name> <word,...>"},
    {"command": "setcondition", "description": "Filter by condition: /setcondition <name> <condition>"},
    {"command": "setmaxage", "description": "Filter by listing age: /setmaxage <name> <days>"},
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


async def set_bot_commands(client: httpx.AsyncClient, bot_token: str) -> None:
    response = await client.post(
        f"{TELEGRAM_API_BASE}/bot{bot_token}/setMyCommands",
        json={"commands": BOT_COMMANDS},
        timeout=10,
    )
    response.raise_for_status()


async def get_updates(client: httpx.AsyncClient, bot_token: str, offset: int | None) -> list[dict]:
    params: dict[str, int] = {"timeout": LONG_POLL_TIMEOUT_SECONDS}
    if offset is not None:
        params["offset"] = offset
    response = await client.get(
        f"{TELEGRAM_API_BASE}/bot{bot_token}/getUpdates",
        params=params,
        timeout=LONG_POLL_TIMEOUT_SECONDS + 10,
    )
    response.raise_for_status()
    return response.json()["result"]


async def run_command_listener(bot_token: str, ctx: BotContext) -> None:
    offset: int | None = None
    async with httpx.AsyncClient() as client:
        try:
            await set_bot_commands(client, bot_token)
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
                message = update.get("message")
                if not message or "text" not in message:
                    continue
                chat_id = message["chat"]["id"]
                parsed = parse_command(message["text"])
                if parsed is None:
                    continue
                command, args = parsed
                reply = await dispatch(command, args, chat_id, ctx)
                if reply:
                    ctx.notifier.send_text(chat_id, reply)
