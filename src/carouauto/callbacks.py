from __future__ import annotations

import logging
from dataclasses import dataclass

from . import ui
from .commands import BotContext

logger = logging.getLogger("carouauto")


@dataclass(frozen=True)
class CallbackResult:
    text: str
    reply_markup: dict | None
    toast: str | None = None
    force_reply_prompt: str | None = None


def _not_registered_result() -> CallbackResult:
    return CallbackResult(text="You need to /register first.", reply_markup=None)


def _stale_search_result() -> CallbackResult:
    return CallbackResult(
        text="This search no longer exists.",
        reply_markup=None,
        toast="Search not found",
    )


def _searches_list_result(chat_id: int, ctx: BotContext) -> CallbackResult:
    subs = ctx.subscriptions.list_searches(chat_id)
    if not subs:
        return CallbackResult(
            text="You have no tracked searches yet. Use /add <name> to start one.",
            reply_markup=None,
        )
    lines = ["Your tracked searches:"] + [f"• {sub.name}" for sub in subs]
    return CallbackResult(text="\n".join(lines), reply_markup=ui.searches_list_keyboard(subs))


def _panel_result(chat_id: int, search_id: int, ctx: BotContext) -> CallbackResult:
    sub = ctx.subscriptions.get_search_by_id(chat_id, search_id)
    if sub is None:
        return _stale_search_result()
    return CallbackResult(text=ui.search_panel_text(sub), reply_markup=ui.search_panel_keyboard(sub))


async def dispatch_callback(data: str, chat_id: int, ctx: BotContext) -> CallbackResult:
    try:
        if not ctx.subscriptions.is_active(chat_id):
            return _not_registered_result()

        parts = data.split(":")
        verb = parts[0]

        if verb == "ls":
            return _searches_list_result(chat_id, ctx)

        if verb == "sp":
            return _panel_result(chat_id, int(parts[1]), ctx)

        if verb == "tg":
            search_id, field = int(parts[1]), parts[2]
            # Ownership is checked here, not trusted from callback data: a forged or
            # stale search_id resolves through get_search_by_id (chat_id-scoped) and
            # falls through to the same "no longer exists" response as an unknown id,
            # whether it never existed or belongs to someone else.
            sub = ctx.subscriptions.get_search_by_id(chat_id, search_id)
            if sub is None:
                return _stale_search_result()
            if field == "pa":
                ctx.subscriptions.set_paused(chat_id, sub.name, not sub.paused)
            elif field == "hb":
                ctx.subscriptions.set_hide_bumped(chat_id, sub.name, not sub.hide_bumped)
            return _panel_result(chat_id, search_id, ctx)

        if verb == "cd":
            sub = ctx.subscriptions.get_search_by_id(chat_id, int(parts[1]))
            if sub is None:
                return _stale_search_result()
            return CallbackResult(text=f"Set condition for '{sub.name}':", reply_markup=ui.condition_keyboard(sub))

        if verb == "cds":
            search_id, idx = int(parts[1]), int(parts[2])
            sub = ctx.subscriptions.get_search_by_id(chat_id, search_id)
            if sub is None:
                return _stale_search_result()
            condition = ui.CONDITIONS[idx] if idx < len(ui.CONDITIONS) else None
            ctx.subscriptions.set_condition_filter(chat_id, sub.name, condition)
            return _panel_result(chat_id, search_id, ctx)

        return CallbackResult(text="Unknown action.", reply_markup=None, toast="Unknown action")
    except Exception as exc:
        logger.error("error handling callback '%s': %s", data, type(exc).__name__)
        return CallbackResult(text="Something went wrong.", reply_markup=None, toast="Error")
