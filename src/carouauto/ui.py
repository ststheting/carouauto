from __future__ import annotations

from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from .subscriptions import UserSearch

if TYPE_CHECKING:
    from .scheduler import SearchState

MAX_CALLBACK_DATA_BYTES = 64

# Displayed times (e.g. "last checked") are shown in the user's local time
# rather than UTC, since that's what they actually read.
DISPLAY_TIMEZONE = ZoneInfo("Asia/Singapore")


def status_line(sub: UserSearch, state: SearchState | None) -> str:
    if sub.paused:
        return "paused (by you)"
    if state is not None and state.paused:
        return "paused (Cloudflare challenge, awaiting manual solve)"
    if state is not None and state.last_polled_at:
        local_time = state.last_polled_at.astimezone(DISPLAY_TIMEZONE)
        return f"last checked {local_time.strftime('%H:%M')} SGT"
    return "not yet checked"


def bumped_label(hide_bumped: bool) -> str:
    return "👁 Show bumped" if hide_bumped else "🙈 Hide bumped"


def mute_label(paused: bool) -> str:
    return "🔔 Unmute" if paused else "🔇 Mute this search"


def price_prompt(name: str) -> str:
    return f"Reply with min and max for '{name}', e.g. `100 500`, or `none`."


def exclude_prompt(name: str) -> str:
    return f"Reply with words to exclude for '{name}', comma-separated, or `none`."


def inline_keyboard(rows: list[list[tuple[str, str]]]) -> dict:
    for row in rows:
        for text, callback_data in row:
            if len(callback_data.encode()) > MAX_CALLBACK_DATA_BYTES:
                raise ValueError(
                    f"callback_data '{callback_data}' exceeds Telegram's {MAX_CALLBACK_DATA_BYTES}-byte limit"
                )
    return {
        "inline_keyboard": [
            [{"text": text, "callback_data": data} for text, data in row] for row in rows
        ]
    }


def searches_list_keyboard(subs: list[UserSearch]) -> dict:
    return inline_keyboard([[(sub.name, f"sp:{sub.search_id}")] for sub in subs])


def search_panel_text(sub: UserSearch, state: SearchState | None = None) -> str:
    lines = [f"⚙️ {sub.name}"]
    if sub.min_price is not None or sub.max_price is not None:
        lo = sub.min_price if sub.min_price is not None else "-"
        hi = sub.max_price if sub.max_price is not None else "-"
        lines.append(f"Price: {lo}-{hi}")
    if sub.exclude_keywords:
        lines.append(f"Excluding: {sub.exclude_keywords}")
    if sub.condition_filter:
        lines.append(f"Condition: {sub.condition_filter}")
    lines.append(f"Status: {status_line(sub, state)}")
    return "\n".join(lines)


def search_panel_keyboard(sub: UserSearch) -> dict:
    pause_label = "▶️ Resume" if sub.paused else "⏸ Pause"
    hide_label = bumped_label(sub.hide_bumped)
    condition_label = f"Condition: {sub.condition_filter}" if sub.condition_filter else "Condition: Any"
    return inline_keyboard(
        [
            [(pause_label, f"tg:{sub.search_id}:pa"), (hide_label, f"tg:{sub.search_id}:hb")],
            [(condition_label, f"cd:{sub.search_id}")],
            [("💰 Set price", f"pp:{sub.search_id}"), ("🚫 Exclude words", f"px:{sub.search_id}")],
            [("👀 Current listings", f"li:{sub.search_id}")],
            [("🗑 Remove", f"rm:{sub.search_id}")],
            [("⬅️ Back", "ls")],
        ]
    )


CONDITIONS = ("Brand new", "Like new", "Lightly used", "Well used", "Heavily used")


def condition_keyboard(sub: UserSearch) -> dict:
    def label(text: str, active: bool) -> str:
        return f"✓ {text}" if active else text

    rows = [
        [(label(cond, sub.condition_filter == cond), f"cds:{sub.search_id}:{i}")]
        for i, cond in enumerate(CONDITIONS)
    ]
    rows.append([(label("Any", not sub.condition_filter), f"cds:{sub.search_id}:5")])
    rows.append([("⬅️ Back", f"sp:{sub.search_id}")])
    return inline_keyboard(rows)


def remove_confirm_keyboard(search_id: int) -> dict:
    return inline_keyboard(
        [
            [("Yes, remove it", f"rmy:{search_id}"), ("Cancel", f"rmn:{search_id}")],
        ]
    )


def add_flow_keyboard() -> dict:
    return inline_keyboard(
        [[("✅ Add now", "afy"), ("💰 Set price range first", "afp")], [("Cancel", "afc")]]
    )


def search_picker_keyboard(subs: list[UserSearch], verb: str) -> dict:
    return inline_keyboard([[(sub.name, f"{verb}:{sub.search_id}")] for sub in subs])


def undo_keyboard(undo_verb: str, search_id: int) -> dict:
    return inline_keyboard([[("↩️ Undo", f"{undo_verb}:{search_id}")]])


def back_to_search_keyboard(search_id: int) -> dict:
    return inline_keyboard([[("⬅️ Back to search", f"sp:{search_id}")]])


# Persistent reply-keyboard buttons. A tap arrives as a plain text message
# with exactly this label; the listener maps it to the command via this dict.
MENU_BUTTONS = {
    "🔎 My searches": "searches",
    "➕ Add search": "add",
    "📊 Status": "status",
    "❓ Help": "help",
}


def bottom_keyboard() -> dict:
    labels = list(MENU_BUTTONS)
    return {
        "keyboard": [
            [{"text": labels[0]}, {"text": labels[1]}],
            [{"text": labels[2]}, {"text": labels[3]}],
        ],
        "is_persistent": True,
        "resize_keyboard": True,
    }


def main_menu_keyboard() -> dict:
    return inline_keyboard(
        [
            [("🔎 My searches", "mm:se"), ("➕ Add search", "mm:ad")],
            [("📊 Status", "mm:st"), ("❓ Help", "mm:he")],
        ]
    )


def notification_card_keyboard(search_id: int, url: str, hide_bumped: bool, paused: bool = False) -> dict:
    # Built directly rather than through inline_keyboard(): URL buttons don't
    # fit its (text, callback_data) tuple shape. An empty url (a rebuilt
    # keyboard with no listing to point at) omits the row — Telegram rejects
    # a URL button with an empty url.
    rows = []
    if url:
        rows.append([{"text": "Open listing", "url": url}])
    rows.append(
        [
            {"text": mute_label(paused), "callback_data": f"cmt:{search_id}"},
            {"text": bumped_label(hide_bumped), "callback_data": f"cbb:{search_id}"},
        ]
    )
    rows.append([{"text": "⚙️ Manage search", "callback_data": f"ms:{search_id}"}])
    return {"inline_keyboard": rows}
