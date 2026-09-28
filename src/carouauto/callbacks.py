from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable

from . import ui
from .commands import (
    BotContext,
    PendingInput,
    _build_carousell_search_url,
    _finish_add,
    _is_url,
    current_listings_text,
    handle_status,
)

logger = logging.getLogger("carouauto")

# A marker CallbackResult.text can be set to, meaning "leave the message's
# existing text/caption alone — only the keyboard changed." A photo message's
# content lives in "caption", not "text", so editMessageText can't touch it
# at all. The listener instead calls editMessageReplyMarkup for this case,
# which updates only the keyboard and works uniformly whether the underlying
# message is text or a photo — it never sends this sentinel string anywhere.
CARD_TEXT_UNCHANGED = "\x00__unchanged__\x00"


@dataclass(frozen=True)
class CallbackResult:
    text: str
    reply_markup: dict | None
    toast: str | None = None
    force_reply_prompt: str | None = None
    # Deliver as a fresh message instead of editing the tapped one (a photo
    # card can't be edited into a text panel).
    send_as_new: bool = False
    # Slow work the listener awaits after answering the tap, so Telegram
    # never reports the callback query as too old.
    followup: Callable[[], Awaitable[None]] | None = None


def _not_registered_result() -> CallbackResult:
    return CallbackResult(text="You need to /register first.", reply_markup=None)


def _stale_search_result() -> CallbackResult:
    return CallbackResult(
        text="This search no longer exists.",
        reply_markup=None,
        toast="Search not found",
    )


def _stale_card_result() -> CallbackResult:
    # Same message as _stale_search_result(), but delivered as a new message:
    # the listener edits in place via editMessageText for the plain variant,
    # which on a text notification card would wipe out the listing text and
    # its Open-listing button.
    return CallbackResult(
        text="This search no longer exists.",
        reply_markup=None,
        toast="Search not found",
        send_as_new=True,
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
    return CallbackResult(
        text=ui.search_panel_text(sub, ctx.states.get(sub.url)), reply_markup=ui.search_panel_keyboard(sub)
    )


def _relabel_button(existing_markup: dict, callback_data: str, new_text: str) -> dict:
    # Copies every button so the caller's dict (parsed from the incoming
    # update) is never mutated, and keeps URL buttons exactly as they were.
    rows = [[dict(button) for button in row] for row in existing_markup["inline_keyboard"]]
    for row in rows:
        for button in row:
            if button.get("callback_data") == callback_data:
                button["text"] = new_text
    return {"inline_keyboard": rows}


def _button_text(markup: dict, callback_data: str) -> str | None:
    for row in markup["inline_keyboard"]:
        for button in row:
            if button.get("callback_data") == callback_data:
                return button.get("text")
    return None


# Explicit setters used by the pickers: tap "pa" always pauses (never
# toggles), and the Undo button points at the opposite verb.
_STATE_VERBS = {
    "pa": ("paused", True, "Paused '{name}'.", "re"),
    "re": ("paused", False, "Resumed '{name}'.", "pa"),
    "hb": ("hide_bumped", True, "Hiding bumped listings for '{name}'.", "sb"),
    "sb": ("hide_bumped", False, "Showing bumped listings for '{name}' again.", "hb"),
}


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
                text=ui.search_panel_text(sub, ctx.states.get(sub.url)),
                reply_markup=ui.search_panel_keyboard(sub),
                force_reply_prompt=ui.price_prompt(sub.name),
            )

        if verb == "px":
            sub = ctx.subscriptions.get_search_by_id(chat_id, int(parts[1]))
            if sub is None:
                return _stale_search_result()
            ctx.pending[chat_id] = PendingInput(
                kind="setexclude", search_id=sub.search_id, created_at=datetime.now(timezone.utc)
            )
            return CallbackResult(
                text=ui.search_panel_text(sub, ctx.states.get(sub.url)),
                reply_markup=ui.search_panel_keyboard(sub),
                force_reply_prompt=ui.exclude_prompt(sub.name),
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

        if verb in _STATE_VERBS:
            field_name, value, message, undo_verb = _STATE_VERBS[verb]
            sub = ctx.subscriptions.get_search_by_id(chat_id, int(parts[1]))
            if sub is None:
                return _stale_search_result()
            if field_name == "paused":
                ctx.subscriptions.set_paused(chat_id, sub.name, value)
            else:
                ctx.subscriptions.set_hide_bumped(chat_id, sub.name, value)
            return CallbackResult(
                text=message.format(name=sub.name),
                reply_markup=ui.undo_keyboard(undo_verb, sub.search_id),
            )

        if verb == "li":
            sub = ctx.subscriptions.get_search_by_id(chat_id, int(parts[1]))
            if sub is None:
                return _stale_search_result()

            async def send_listings() -> None:
                try:
                    text = await current_listings_text(sub, ctx)
                except Exception as exc:
                    logger.error("listing fetch failed for search_id=%s: %s", sub.search_id, type(exc).__name__)
                    text = f"Couldn't fetch listings for '{sub.name}' right now — try again shortly."
                ctx.notifier.send_text(chat_id, text)

            return CallbackResult(
                text=f"Fetching current listings for '{sub.name}' — they'll arrive as a new message.",
                reply_markup=ui.back_to_search_keyboard(sub.search_id),
                toast="Fetching…",
                followup=send_listings,
            )

        if verb == "ms":
            sub = ctx.subscriptions.get_search_by_id(chat_id, int(parts[1]))
            if sub is None:
                return _stale_card_result()
            return CallbackResult(
                text=ui.search_panel_text(sub, ctx.states.get(sub.url)),
                reply_markup=ui.search_panel_keyboard(sub),
                send_as_new=True,
            )

        if verb == "afy":
            pending = ctx.pending.get(chat_id)
            if pending is None or pending.kind not in ("add_query", "add_price") or not pending.query:
                return CallbackResult(text="That add flow expired. Send /add to start again.", reply_markup=None)
            query = pending.query
            del ctx.pending[chat_id]
            url = query if _is_url(query) else _build_carousell_search_url(query)
            text, markup = _finish_add(chat_id, query, url, None, None, ctx)
            return CallbackResult(text=text, reply_markup=markup)

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
                return _stale_card_result()
            if current_markup is None:
                target_paused = not sub.paused
                markup = ui.notification_card_keyboard(sub.search_id, "", sub.hide_bumped, target_paused)
            else:
                # The tapped button's own label is the source of truth, not sub.paused:
                # a card sent before a deploy, or updated elsewhere (panel, /pause,
                # another card) since it was sent, can show a stale label.
                current_label = _button_text(current_markup, f"cmt:{sub.search_id}")
                target_paused = False if current_label == ui.mute_label(True) else True
                markup = _relabel_button(current_markup, f"cmt:{sub.search_id}", ui.mute_label(target_paused))
            ctx.subscriptions.set_paused(chat_id, sub.name, target_paused)
            toast = f"Muted '{sub.name}'." if target_paused else f"Unmuted '{sub.name}'."
            return CallbackResult(text=CARD_TEXT_UNCHANGED, reply_markup=markup, toast=toast)

        if verb == "cbb":
            sub = ctx.subscriptions.get_search_by_id(chat_id, int(parts[1]))
            if sub is None:
                return _stale_card_result()
            if current_markup is None:
                target_hide_bumped = not sub.hide_bumped
                markup = ui.notification_card_keyboard(sub.search_id, "", target_hide_bumped, sub.paused)
            else:
                current_label = _button_text(current_markup, f"cbb:{sub.search_id}")
                target_hide_bumped = False if current_label == ui.bumped_label(True) else True
                markup = _relabel_button(current_markup, f"cbb:{sub.search_id}", ui.bumped_label(target_hide_bumped))
            ctx.subscriptions.set_hide_bumped(chat_id, sub.name, target_hide_bumped)
            return CallbackResult(text=CARD_TEXT_UNCHANGED, reply_markup=markup, toast="Updated.")

        if verb == "mm":
            action = parts[1]
            if action == "se":
                return _searches_list_result(chat_id, ctx)
            if action == "ad":
                ctx.pending[chat_id] = PendingInput(
                    kind="add_query", search_id=None, created_at=datetime.now(timezone.utc)
                )
                return CallbackResult(
                    text="Adding a search.",
                    reply_markup=None,
                    force_reply_prompt="What should I search for?",
                )
            if action == "st":
                text = await handle_status([], chat_id, ctx)
                return CallbackResult(text=text, reply_markup=None)
            if action == "he":
                minutes = round(ctx.poll_interval_seconds / 60)
                text = (
                    "carouauto — Carousell listing monitor\n\n"
                    f"Checks run roughly every {minutes} minutes.\n\n"
                    "/register <password> — get access\n"
                    "Use the buttons from /searches to manage what you track."
                )
                return CallbackResult(text=text, reply_markup=None)

        return CallbackResult(text="Unknown action.", reply_markup=None, toast="Unknown action")
    except Exception as exc:
        logger.error("error handling callback '%s': %s", data, type(exc).__name__)
        return CallbackResult(text="Something went wrong.", reply_markup=None, toast="Error")
