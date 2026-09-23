from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from . import ui
from .commands import BotContext, PendingInput, _build_carousell_search_url, _finish_add, _is_url

logger = logging.getLogger("carouauto")

# A marker CallbackResult.text can be set to, meaning "leave the message's
# existing text alone — only the keyboard changed." editMessageText requires
# a text value, so the listener substitutes the message's own current text
# instead of actually sending this string.
CARD_TEXT_UNCHANGED = "\x00__unchanged__\x00"


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


def _card_keyboard_with_updated_bumped_label(existing_markup: dict, search_id: int, hide_bumped: bool) -> dict:
    bumped_label = "👁 Show bumped" if hide_bumped else "🙈 Hide bumped"
    rows = [row[:] for row in existing_markup["inline_keyboard"]]
    for row in rows:
        for button in row:
            if button.get("callback_data") == f"cbb:{search_id}":
                button["text"] = bumped_label
    return {"inline_keyboard": rows}


async def dispatch_callback(
    data: str, chat_id: int, ctx: BotContext, current_markup: dict | None = None
) -> CallbackResult:
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

        if verb == "pp":
            sub = ctx.subscriptions.get_search_by_id(chat_id, int(parts[1]))
            if sub is None:
                return _stale_search_result()
            ctx.pending[chat_id] = PendingInput(
                kind="setprice", search_id=sub.search_id, created_at=datetime.now(timezone.utc)
            )
            return CallbackResult(
                text=ui.search_panel_text(sub),
                reply_markup=ui.search_panel_keyboard(sub),
                force_reply_prompt=f"Reply with min and max for '{sub.name}', e.g. `100 500`, or `none`.",
            )

        if verb == "px":
            sub = ctx.subscriptions.get_search_by_id(chat_id, int(parts[1]))
            if sub is None:
                return _stale_search_result()
            ctx.pending[chat_id] = PendingInput(
                kind="setexclude", search_id=sub.search_id, created_at=datetime.now(timezone.utc)
            )
            return CallbackResult(
                text=ui.search_panel_text(sub),
                reply_markup=ui.search_panel_keyboard(sub),
                force_reply_prompt=f"Reply with words to exclude for '{sub.name}', comma-separated, or `none`.",
            )

        if verb == "rm":
            sub = ctx.subscriptions.get_search_by_id(chat_id, int(parts[1]))
            if sub is None:
                return _stale_search_result()
            return CallbackResult(
                text=f"Remove '{sub.name}'? Are you sure?",
                reply_markup=ui.remove_confirm_keyboard(sub.search_id),
            )

        if verb == "rmy":
            search_id = int(parts[1])
            sub = ctx.subscriptions.get_search_by_id(chat_id, search_id)
            if sub is None:
                return _stale_search_result()
            ctx.subscriptions.remove_search(chat_id, sub.name)
            ctx.seen_store.delete_all_for_search(search_id)
            return CallbackResult(text=f"Removed '{sub.name}'.", reply_markup=None)

        if verb == "rmn":
            return _panel_result(chat_id, int(parts[1]), ctx)

        if verb == "afy":
            pending = ctx.pending.get(chat_id)
            if pending is None or pending.kind not in ("add_query", "add_price") or not pending.query:
                return CallbackResult(text="That add flow expired. Send /add to start again.", reply_markup=None)
            query = pending.query
            del ctx.pending[chat_id]
            url = query if _is_url(query) else _build_carousell_search_url(query)
            text = _finish_add(chat_id, query, url, None, None, ctx)
            return CallbackResult(text=text, reply_markup=None)

        if verb == "afp":
            pending = ctx.pending.get(chat_id)
            if pending is None or not pending.query:
                return CallbackResult(text="That add flow expired. Send /add to start again.", reply_markup=None)
            ctx.pending[chat_id] = PendingInput(
                kind="add_price", search_id=None, created_at=datetime.now(timezone.utc), query=pending.query
            )
            return CallbackResult(
                text=f"Tracking '{pending.query}'.",
                reply_markup=None,
                force_reply_prompt="Reply with min and max, e.g. `100 500`, or `none` for no price filter.",
            )

        if verb == "afc":
            ctx.pending.pop(chat_id, None)
            return CallbackResult(text="Cancelled.", reply_markup=None)

        if verb == "cmt":
            sub = ctx.subscriptions.get_search_by_id(chat_id, int(parts[1]))
            if sub is None:
                return _stale_search_result()
            ctx.subscriptions.set_paused(chat_id, sub.name, True)
            markup = current_markup or ui.notification_card_keyboard(sub.search_id, "", sub.hide_bumped)
            return CallbackResult(text=CARD_TEXT_UNCHANGED, reply_markup=markup, toast=f"Muted '{sub.name}'.")

        if verb == "cbb":
            sub = ctx.subscriptions.get_search_by_id(chat_id, int(parts[1]))
            if sub is None:
                return _stale_search_result()
            ctx.subscriptions.set_hide_bumped(chat_id, sub.name, not sub.hide_bumped)
            updated = ctx.subscriptions.get_search_by_id(chat_id, sub.search_id)
            markup = current_markup or ui.notification_card_keyboard(updated.search_id, "", updated.hide_bumped)
            markup = _card_keyboard_with_updated_bumped_label(markup, updated.search_id, updated.hide_bumped)
            return CallbackResult(text=CARD_TEXT_UNCHANGED, reply_markup=markup, toast="Updated.")

        return CallbackResult(text="Unknown action.", reply_markup=None, toast="Unknown action")
    except Exception as exc:
        logger.error("error handling callback '%s': %s", data, type(exc).__name__)
        return CallbackResult(text="Something went wrong.", reply_markup=None, toast="Error")
