from __future__ import annotations

from .subscriptions import UserSearch

MAX_CALLBACK_DATA_BYTES = 64


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


def search_panel_text(sub: UserSearch) -> str:
    lines = [f"⚙️ {sub.name}"]
    if sub.min_price is not None or sub.max_price is not None:
        lo = sub.min_price if sub.min_price is not None else "-"
        hi = sub.max_price if sub.max_price is not None else "-"
        lines.append(f"Price: {lo}-{hi}")
    if sub.exclude_keywords:
        lines.append(f"Excluding: {sub.exclude_keywords}")
    if sub.condition_filter:
        lines.append(f"Condition: {sub.condition_filter}")
    if sub.paused:
        lines.append("Status: paused (tap ▶️ Resume to continue notifications)")
    else:
        lines.append("Status: active")
    return "\n".join(lines)


def search_panel_keyboard(sub: UserSearch) -> dict:
    pause_label = "▶️ Resume" if sub.paused else "⏸ Pause"
    bumped_label = "👁 Show bumped" if sub.hide_bumped else "🙈 Hide bumped"
    condition_label = f"Condition: {sub.condition_filter}" if sub.condition_filter else "Condition: Any"
    return inline_keyboard(
        [
            [(pause_label, f"tg:{sub.search_id}:pa"), (bumped_label, f"tg:{sub.search_id}:hb")],
            [(condition_label, f"cd:{sub.search_id}")],
            [("💰 Set price", f"pp:{sub.search_id}"), ("🚫 Exclude words", f"px:{sub.search_id}")],
            [("🗑 Remove", f"rm:{sub.search_id}")],
            [("⬅️ Back", "ls")],
        ]
    )


CONDITIONS = ("Brand new", "Like new", "Lightly used", "Well used", "Heavily used")


def condition_keyboard(sub: UserSearch) -> dict:
    rows = [[(cond, f"cds:{sub.search_id}:{i}")] for i, cond in enumerate(CONDITIONS)]
    rows.append([("Any", f"cds:{sub.search_id}:5")])
    rows.append([("⬅️ Back", f"sp:{sub.search_id}")])
    return inline_keyboard(rows)


def remove_confirm_keyboard(search_id: int) -> dict:
    return inline_keyboard(
        [
            [("Yes, remove it", f"rmy:{search_id}"), ("Cancel", f"rmn:{search_id}")],
        ]
    )
