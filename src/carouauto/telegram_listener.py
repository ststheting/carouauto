from __future__ import annotations

import asyncio
import logging

import httpx

from .commands import BotContext, dispatch, parse_command

logger = logging.getLogger("carouauto")

TELEGRAM_API_BASE = "https://api.telegram.org"
LONG_POLL_TIMEOUT_SECONDS = 30


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
                logger.error("unexpected error in command listener: %s", exc)
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
