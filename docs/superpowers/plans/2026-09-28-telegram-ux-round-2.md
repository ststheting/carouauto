# Telegram UX Round 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop making users remember and type search names — pickers for every name-taking command, a richer search panel, smarter notification cards, and an always-visible bottom keyboard.

**Architecture:** Extends the existing button machinery (`ui.py` pure builders → `callbacks.py` verb routing → `telegram_listener.py` delivery). `CallbackResult` gains `send_as_new` and `followup` so a tap can open a new message or do slow work after it is answered. Typed commands keep their full-argument behavior; only the "no name / unknown name / missing values" paths change.

**Tech Stack:** Python 3.11+, httpx, pytest + pytest-asyncio. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-28-telegram-ux-round-2-design.md`

## Global Constraints

- Callback data ≤ 64 bytes — every inline keyboard goes through `ui.inline_keyboard()` except `ui.notification_card_keyboard` (URL buttons don't fit its tuple shape), whose callback data is `cmt:<id>` / `cbb:<id>` / `ms:<id>`.
- Every callback verb that takes a search id resolves it through `ctx.subscriptions.get_search_by_id(chat_id, search_id)` and returns `_stale_search_result()` on `None`. Never trust the id in callback data.
- Only `type(exc).__name__` may reach a log line or exception message — never `str(exc)` (httpx messages embed the bot token).
- `dispatch_callback` never raises; every listener step after dispatch is independently guarded.
- Handlers that need a keyboard or prompt call `ctx.notifier.send_text(...)` directly and return `""`; plain-text replies are returned as strings (existing pattern).
- No schema change. No new dependency.
- Tests: `.venv/bin/python -m pytest -q` must be fully green before every commit.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Tasks 1–8 commit only; Task 9 pushes and deploys. GitHub pushes: `gh auth switch --hostname github.com --user ststheting` → `git push origin main` → `gh auth switch --hostname github.com --user hmishaun` (switch back even if the push fails).

## Review Focus

1. **Search names with spaces or emoji in pickers** — button text is the name, callback data uses the numeric id, so `"nintendo switch 🎮"` must still produce `pa:<id>` well under 64 bytes. Pinned in Task 2.
2. **Case-mismatched typed name** — `/pause Speediance` when the stored name is `speediance` must show the picker ("No search named 'Speediance' — pick one:"), not silently do nothing or error. Pinned in Task 6.
3. **Revoked user tapping a bottom-keyboard button** — the persistent keyboard stays on their screen after `/revoke`; a tap must answer "You need to /register first." via the normal dispatch gate. Pinned in Task 8.
4. **Zero searches** — a picker command or "🔎 My searches" with no tracked searches must reply with the "no tracked searches" text, never an empty keyboard. Pinned in Tasks 6 and 8.
5. **Listing fetch raises (browser dead / timeout) from a `li` tap** — user gets "Couldn't fetch listings…" as a message; nothing propagates. Pinned in Task 4.

---

## File Structure

- `src/carouauto/ui.py` — pure builders. Gains: `DISPLAY_TIMEZONE`, `status_line`, `bumped_label`, `mute_label`, `search_picker_keyboard`, `undo_keyboard`, `back_to_search_keyboard`, `price_prompt`, `exclude_prompt`, `MENU_BUTTONS`, `bottom_keyboard`; changes to `search_panel_text`, `search_panel_keyboard`, `condition_keyboard`, `notification_card_keyboard`.
- `src/carouauto/callbacks.py` — `CallbackResult` fields; verbs `pa`/`re`/`hb`/`sb`/`li`/`ms`; `cmt` becomes a toggle.
- `src/carouauto/commands.py` — `current_listings_text`, `_send_picker`, `_start_prompt`, `_reply_with`; `_finish_add` returns `(text, markup)`; picker behavior in name-taking handlers; bottom keyboard in `/start`/`/register`/`/help`.
- `src/carouauto/telegram_listener.py` — `send_as_new` / `followup` delivery; menu-button routing; `_apply_pending_add_price` renders the panel.
- Tests: `tests/test_ui.py`, `tests/test_callbacks.py`, `tests/test_commands.py`, `tests/test_telegram_listener.py`.

---

### Task 1: Shared status line, richer panel text, ✓ on active condition

**Files:**
- Modify: `src/carouauto/ui.py`
- Modify: `src/carouauto/commands.py` (remove `DISPLAY_TIMEZONE`/`ZoneInfo`; `handle_status` uses `ui.status_line`)
- Modify: `src/carouauto/callbacks.py` (`_panel_result`, `pp`, `px` pass state)
- Modify: `src/carouauto/telegram_listener.py` (`_apply_pending_reply` passes state)
- Test: `tests/test_ui.py`, `tests/test_callbacks.py`

**Interfaces:**
- Produces: `ui.DISPLAY_TIMEZONE`; `ui.status_line(sub: UserSearch, state: SearchState | None) -> str`; `ui.search_panel_text(sub: UserSearch, state: SearchState | None = None) -> str`; panel keyboard gains `li:<id>`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_ui.py`:

```python
from datetime import datetime, timezone

from carouauto.scheduler import SearchState


def test_status_line_paused_by_user():
    assert ui.status_line(make_sub(paused=True), None) == "paused (by you)"


def test_status_line_cloudflare_pause():
    assert ui.status_line(make_sub(), SearchState(paused=True)) == (
        "paused (Cloudflare challenge, awaiting manual solve)"
    )


def test_status_line_last_checked_in_singapore_time():
    state = SearchState(last_polled_at=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc))
    assert ui.status_line(make_sub(), state) == "last checked 20:00 SGT"


def test_status_line_not_yet_checked():
    assert ui.status_line(make_sub(), None) == "not yet checked"
    assert ui.status_line(make_sub(), SearchState()) == "not yet checked"


def test_search_panel_text_includes_the_status_line():
    state = SearchState(last_polled_at=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc))
    text = ui.search_panel_text(make_sub(name="speediance"), state)
    assert "Status: last checked 20:00 SGT" in text


def test_search_panel_keyboard_has_a_current_listings_button():
    markup = ui.search_panel_keyboard(make_sub(search_id=7))
    flat = {b["callback_data"]: b["text"] for row in markup["inline_keyboard"] for b in row}
    assert flat["li:7"] == "👀 Current listings"


def test_condition_keyboard_marks_the_active_condition():
    markup = ui.condition_keyboard(make_sub(search_id=3, condition_filter="Like new"))
    texts = [b["text"] for row in markup["inline_keyboard"] for b in row]
    assert "✓ Like new" in texts
    assert "Brand new" in texts
    assert "Any" in texts
```

Replace the body of the existing `test_condition_keyboard_has_one_button_per_condition_plus_any` (no condition set → "Any" is active):

```python
def test_condition_keyboard_has_one_button_per_condition_plus_any():
    sub = make_sub(search_id=3)

    markup = ui.condition_keyboard(sub)

    flat = [b for row in markup["inline_keyboard"] for b in row]
    assert [b["text"] for b in flat[:-2]] == list(ui.CONDITIONS)
    assert flat[-2]["text"] == "✓ Any"
    assert flat[-2]["callback_data"] == "cds:3:5"
    assert flat[-1]["callback_data"] == "sp:3"  # back to panel
```

In `tests/test_callbacks.py`, `test_tg_pa_toggles_pause_and_re_renders_the_panel` currently asserts `"resume" in result.text.lower() or "▶" in result.text`. The panel now shows the shared status line, so change that one line to:

```python
    assert "paused" in result.text.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_ui.py tests/test_callbacks.py -q`
Expected: FAIL — `AttributeError: module 'carouauto.ui' has no attribute 'status_line'`, and the ✓ assertions fail.

- [ ] **Step 3: Implement**

In `src/carouauto/ui.py`, replace the header imports and add the helper:

```python
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
```

Replace `search_panel_text`:

```python
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
```

In `search_panel_keyboard`, insert a row before the Remove row:

```python
            [("💰 Set price", f"pp:{sub.search_id}"), ("🚫 Exclude words", f"px:{sub.search_id}")],
            [("👀 Current listings", f"li:{sub.search_id}")],
            [("🗑 Remove", f"rm:{sub.search_id}")],
```

Replace `condition_keyboard`:

```python
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
```

In `src/carouauto/commands.py`: delete the `from zoneinfo import ZoneInfo` import, the `DISPLAY_TIMEZONE` comment and constant. Replace the loop in `handle_status`:

```python
    for sub in subs:
        lines.append(f"{sub.name}: {ui.status_line(sub, ctx.states.get(sub.url))}")
    return "\n".join(lines)
```

Pass state to every existing `search_panel_text` call:
- `src/carouauto/callbacks.py` `_panel_result`: `ui.search_panel_text(sub, ctx.states.get(sub.url))`
- `src/carouauto/callbacks.py` `pp` and `px` branches: `text=ui.search_panel_text(sub, ctx.states.get(sub.url)),`
- `src/carouauto/telegram_listener.py` `_apply_pending_reply`, last line: `ui.search_panel_text(updated, ctx.states.get(updated.url))`

Run `grep -rn "DISPLAY_TIMEZONE\|search_panel_text(" src tests` and confirm no caller was missed.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (full suite; the existing `/status` tests — "paused", "you", "challenge", "20:00 SGT" — still pass unchanged).

- [ ] **Step 5: Commit**

```bash
git add src/carouauto/ui.py src/carouauto/commands.py src/carouauto/callbacks.py src/carouauto/telegram_listener.py tests/test_ui.py tests/test_callbacks.py
git commit -m "$(cat <<'EOF'
feat: shared status line on the search panel, ✓ on the active condition

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: New keyboards and shared labels

**Files:**
- Modify: `src/carouauto/ui.py`
- Modify: `src/carouauto/callbacks.py` (`pp`/`px` use `ui.price_prompt`/`ui.exclude_prompt`)
- Test: `tests/test_ui.py`

**Interfaces:**
- Produces: `ui.bumped_label(hide_bumped: bool) -> str`; `ui.mute_label(paused: bool) -> str`; `ui.search_picker_keyboard(subs: list[UserSearch], verb: str) -> dict`; `ui.undo_keyboard(undo_verb: str, search_id: int) -> dict`; `ui.back_to_search_keyboard(search_id: int) -> dict`; `ui.price_prompt(name: str) -> str`; `ui.exclude_prompt(name: str) -> str`; `ui.MENU_BUTTONS: dict[str, str]` (label → command, ordered); `ui.bottom_keyboard() -> dict`; `ui.notification_card_keyboard(search_id: int, url: str, hide_bumped: bool, paused: bool = False) -> dict` (omits the Open row when `url` is empty; adds `ms:<id>`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_ui.py`:

```python
def test_search_picker_keyboard_uses_the_verb_and_search_id():
    subs = [make_sub(search_id=1, name="speediance"), make_sub(search_id=2, name="switch")]

    markup = ui.search_picker_keyboard(subs, "pa")

    buttons = [row[0] for row in markup["inline_keyboard"]]
    assert [(b["text"], b["callback_data"]) for b in buttons] == [("speediance", "pa:1"), ("switch", "pa:2")]


def test_search_picker_keyboard_handles_long_emoji_names():
    sub = make_sub(search_id=987654321, name="nintendo switch 🎮 " * 10)

    markup = ui.search_picker_keyboard([sub], "pa")

    data = markup["inline_keyboard"][0][0]["callback_data"]
    assert data == "pa:987654321"
    assert len(data.encode()) <= ui.MAX_CALLBACK_DATA_BYTES


def test_undo_keyboard_points_at_the_opposite_verb():
    markup = ui.undo_keyboard("re", 4)
    assert markup["inline_keyboard"] == [[{"text": "↩️ Undo", "callback_data": "re:4"}]]


def test_back_to_search_keyboard_opens_the_panel():
    markup = ui.back_to_search_keyboard(4)
    assert markup["inline_keyboard"][0][0]["callback_data"] == "sp:4"


def test_prompts_name_the_search():
    assert "'speediance'" in ui.price_prompt("speediance")
    assert "none" in ui.price_prompt("speediance")
    assert "'speediance'" in ui.exclude_prompt("speediance")


def test_bottom_keyboard_shape_and_labels():
    markup = ui.bottom_keyboard()

    assert markup["is_persistent"] is True
    assert markup["resize_keyboard"] is True
    labels = [b["text"] for row in markup["keyboard"] for b in row]
    assert labels == ["🔎 My searches", "➕ Add search", "📊 Status", "❓ Help"]
    assert list(ui.MENU_BUTTONS) == labels
    assert ui.MENU_BUTTONS == {
        "🔎 My searches": "searches",
        "➕ Add search": "add",
        "📊 Status": "status",
        "❓ Help": "help",
    }


def test_notification_card_keyboard_has_manage_and_mute_buttons():
    markup = ui.notification_card_keyboard(9, "https://www.carousell.sg/p/x-1/", hide_bumped=True)

    flat = [b for row in markup["inline_keyboard"] for b in row]
    assert flat[0] == {"text": "Open listing", "url": "https://www.carousell.sg/p/x-1/"}
    by_data = {b["callback_data"]: b["text"] for b in flat if "callback_data" in b}
    assert by_data["cmt:9"] == "🔇 Mute this search"
    assert by_data["cbb:9"] == "👁 Show bumped"
    assert by_data["ms:9"] == "⚙️ Manage search"


def test_notification_card_keyboard_unmute_label_when_paused():
    markup = ui.notification_card_keyboard(9, "https://x", hide_bumped=True, paused=True)
    flat = [b for row in markup["inline_keyboard"] for b in row]
    assert any(b.get("callback_data") == "cmt:9" and b["text"] == "🔔 Unmute" for b in flat)


def test_notification_card_keyboard_omits_open_row_without_a_url():
    markup = ui.notification_card_keyboard(9, "", hide_bumped=True)
    flat = [b for row in markup["inline_keyboard"] for b in row]
    assert all("url" not in b for b in flat)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_ui.py -q`
Expected: FAIL — missing `search_picker_keyboard`, `bottom_keyboard`, etc.

- [ ] **Step 3: Implement**

In `src/carouauto/ui.py` add after `status_line`:

```python
def bumped_label(hide_bumped: bool) -> str:
    return "👁 Show bumped" if hide_bumped else "🙈 Hide bumped"


def mute_label(paused: bool) -> str:
    return "🔔 Unmute" if paused else "🔇 Mute this search"


def price_prompt(name: str) -> str:
    return f"Reply with min and max for '{name}', e.g. `100 500`, or `none`."


def exclude_prompt(name: str) -> str:
    return f"Reply with words to exclude for '{name}', comma-separated, or `none`."
```

In `search_panel_keyboard`, replace the `bumped_label = ...` local with a call to the new helper (keep the variable name local but different to avoid shadowing):

```python
    hide_label = bumped_label(sub.hide_bumped)
```

and use `(hide_label, f"tg:{sub.search_id}:hb")` in the first row.

Add near the other keyboards:

```python
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
```

Replace `notification_card_keyboard`:

```python
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
```

In `src/carouauto/callbacks.py`, the `pp` and `px` branches: replace the literal `force_reply_prompt=f"..."` strings with `force_reply_prompt=ui.price_prompt(sub.name)` and `force_reply_prompt=ui.exclude_prompt(sub.name)`. In `_card_keyboard_with_updated_bumped_label` replace the local label expression with `bumped = ui.bumped_label(hide_bumped)` and assign `button["text"] = bumped`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (existing `test_notifier.py` card tests still find the url/`cmt`/`cbb` buttons).

- [ ] **Step 5: Commit**

```bash
git add src/carouauto/ui.py src/carouauto/callbacks.py tests/test_ui.py
git commit -m "$(cat <<'EOF'
feat: picker, undo, bottom-keyboard and manage-card keyboard builders

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `CallbackResult.send_as_new` and `followup` delivery

**Files:**
- Modify: `src/carouauto/callbacks.py`
- Modify: `src/carouauto/telegram_listener.py`
- Test: `tests/test_telegram_listener.py`

**Interfaces:**
- Produces: `CallbackResult(..., send_as_new: bool = False, followup: Callable[[], Awaitable[None]] | None = None)`. Listener order: dispatch → deliver (send if `send_as_new`, else edit / edit-markup, with the existing fallback — skipped for `send_as_new` and `CARD_TEXT_UNCHANGED`) → `answer_callback` → force-reply prompt → `await followup()`. Each guarded.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_telegram_listener.py`:

```python
from carouauto.callbacks import CallbackResult


def _callback_update(data, chat_id=111, message_id=42):
    return {
        "update_id": 9,
        "callback_query": {
            "id": "cbq9",
            "data": data,
            "message": {"chat": {"id": chat_id}, "message_id": message_id, "text": "old"},
        },
    }


@pytest.mark.asyncio
async def test_send_as_new_sends_a_fresh_message_instead_of_editing(tmp_path, monkeypatch):
    ctx = make_ctx(tmp_path)

    async def fake_dispatch(data, chat_id, ctx, current_markup=None):
        return CallbackResult(text="panel", reply_markup={"inline_keyboard": []}, send_as_new=True)

    monkeypatch.setattr("carouauto.telegram_listener.dispatch_callback", fake_dispatch)

    await handle_update(_callback_update("ms:1"), ctx)

    assert ctx.notifier.sent == [(111, "panel", {"inline_keyboard": []})]
    assert ctx.notifier.edits == []
    assert ctx.notifier.answers == [("cbq9", None)]


@pytest.mark.asyncio
async def test_followup_runs_after_the_callback_is_answered(tmp_path, monkeypatch):
    ctx = make_ctx(tmp_path)
    answered_before_followup = []

    async def followup():
        answered_before_followup.append(len(ctx.notifier.answers))
        ctx.notifier.send_text(111, "listings")

    async def fake_dispatch(data, chat_id, ctx, current_markup=None):
        return CallbackResult(text="Fetching…", reply_markup=None, followup=followup)

    monkeypatch.setattr("carouauto.telegram_listener.dispatch_callback", fake_dispatch)

    await handle_update(_callback_update("li:1"), ctx)

    assert answered_before_followup == [1]
    assert ctx.notifier.sent[-1] == (111, "listings", None)


@pytest.mark.asyncio
async def test_a_raising_followup_is_contained(tmp_path, monkeypatch):
    ctx = make_ctx(tmp_path)

    async def followup():
        raise RuntimeError("browser died")

    async def fake_dispatch(data, chat_id, ctx, current_markup=None):
        return CallbackResult(text="Fetching…", reply_markup=None, followup=followup)

    monkeypatch.setattr("carouauto.telegram_listener.dispatch_callback", fake_dispatch)

    await handle_update(_callback_update("li:1"), ctx)  # must not raise

    assert ctx.notifier.answers == [("cbq9", None)]


@pytest.mark.asyncio
async def test_a_failed_send_as_new_is_logged_not_retried(tmp_path, monkeypatch):
    ctx = make_ctx(tmp_path)
    ctx.notifier.raise_on_send_text = True

    async def fake_dispatch(data, chat_id, ctx, current_markup=None):
        return CallbackResult(text="panel", reply_markup=None, send_as_new=True)

    monkeypatch.setattr("carouauto.telegram_listener.dispatch_callback", fake_dispatch)

    await handle_update(_callback_update("ms:1"), ctx)  # must not raise

    assert ctx.notifier.edits == []
    assert ctx.notifier.answers == [("cbq9", None)]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_telegram_listener.py -q`
Expected: FAIL — `TypeError: CallbackResult.__init__() got an unexpected keyword argument 'send_as_new'`.

- [ ] **Step 3: Implement**

In `src/carouauto/callbacks.py`, add `from typing import Awaitable, Callable` and extend the dataclass:

```python
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
```

In `src/carouauto/telegram_listener.py`, replace the delivery `try` block in the callback branch of `handle_update` (from `try:` down to the end of the fallback `except`) with:

```python
        try:
            if result.send_as_new:
                ctx.notifier.send_text(chat_id, result.text, result.reply_markup)
            elif result.text == CARD_TEXT_UNCHANGED:
                # A notification card's text/caption must stay exactly as it
                # was — editMessageReplyMarkup updates only the keyboard and
                # works the same whether the underlying message is text or a
                # photo (a photo message has no "text" field to substitute).
                ctx.notifier.edit_reply_markup(chat_id, message_id, result.reply_markup)
            else:
                ctx.notifier.edit_message(chat_id, message_id, result.text, result.reply_markup)
        except Exception as exc:
            logger.error("failed to deliver result for callback '%s': %s", data, type(exc).__name__)
            # Spec: a failed edit falls back to sending a fresh message — the
            # original may be too old to edit, or deleted. Not for
            # CARD_TEXT_UNCHANGED (only a sentinel, no real text) and not for
            # send_as_new (that already was a fresh send; retrying it would
            # just fail the same way).
            if not result.send_as_new and result.text != CARD_TEXT_UNCHANGED:
                try:
                    ctx.notifier.send_text(chat_id, result.text, result.reply_markup)
                except Exception as exc2:
                    logger.error(
                        "failed to send fallback message for callback '%s': %s", data, type(exc2).__name__
                    )
```

After the existing force-reply block at the end of the callback branch, add:

```python
        if result.followup is not None:
            try:
                await result.followup()
            except Exception as exc:
                logger.error("followup failed for callback '%s': %s", data, type(exc).__name__)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (existing edit-fallback tests still pass — the log message text changed, not the behavior).

- [ ] **Step 5: Commit**

```bash
git add src/carouauto/callbacks.py src/carouauto/telegram_listener.py tests/test_telegram_listener.py
git commit -m "$(cat <<'EOF'
feat: CallbackResult can send a new message and run slow follow-up work

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: New verbs `pa`/`re`/`hb`/`sb`/`li`/`ms`, mute becomes a toggle

**Files:**
- Modify: `src/carouauto/commands.py` (extract `current_listings_text`; `handle_list` uses it)
- Modify: `src/carouauto/callbacks.py`
- Test: `tests/test_callbacks.py`

**Interfaces:**
- Consumes: `ui.undo_keyboard`, `ui.back_to_search_keyboard`, `ui.mute_label`, `ui.bumped_label`, `ui.notification_card_keyboard` (Task 2); `CallbackResult.send_as_new` / `followup` (Task 3).
- Produces: `commands.current_listings_text(sub: UserSearch, ctx: BotContext) -> str` (async). Verbs: `pa:<id>`, `re:<id>`, `hb:<id>`, `sb:<id>` (explicit set + Undo), `li:<id>` (followup sends listings), `ms:<id>` (panel, `send_as_new`); `cmt:<id>` toggles.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_callbacks.py`:

```python
class RecordingNotifier:
    def __init__(self):
        self.sent = []

    def send_text(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text, reply_markup))


LISTING_HTML = """
<div data-testid="listing-card-1">
  <a href="/u/seller1/"><p data-testid="listing-card-text-seller-name">seller1</p><div><p>2 minutes ago</p></div></a>
  <a href="/p/item-one-1/"><img alt="Item One" src="https://example.com/1.jpg"/><p>Item One</p><div><p title="S$10">S$10</p></div><p>Brand new</p></a>
</div>
"""


def _registered_ctx_with_search(tmp_path, name="speediance"):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    search_id = ctx.subscriptions.add_search(111, name, "https://example.com/s")
    return ctx, search_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "verb, field, start, expected, undo",
    [
        ("pa", "paused", False, True, "re"),
        ("re", "paused", True, False, "pa"),
        ("hb", "hide_bumped", False, True, "sb"),
        ("sb", "hide_bumped", True, False, "hb"),
    ],
)
async def test_state_verbs_set_explicitly_and_offer_undo(tmp_path, verb, field, start, expected, undo):
    ctx, search_id = _registered_ctx_with_search(tmp_path)
    if field == "paused":
        ctx.subscriptions.set_paused(111, "speediance", start)
    else:
        ctx.subscriptions.set_hide_bumped(111, "speediance", start)

    result = await dispatch_callback(f"{verb}:{search_id}", 111, ctx)

    assert getattr(ctx.subscriptions.get_search_by_id(111, search_id), field) is expected
    assert "speediance" in result.text
    assert result.reply_markup["inline_keyboard"] == [[{"text": "↩️ Undo", "callback_data": f"{undo}:{search_id}"}]]


@pytest.mark.asyncio
async def test_pa_is_idempotent_not_a_toggle(tmp_path):
    ctx, search_id = _registered_ctx_with_search(tmp_path)

    await dispatch_callback(f"pa:{search_id}", 111, ctx)
    await dispatch_callback(f"pa:{search_id}", 111, ctx)

    assert ctx.subscriptions.get_search_by_id(111, search_id).paused is True


@pytest.mark.asyncio
@pytest.mark.parametrize("verb", ["pa", "re", "hb", "sb", "li", "ms"])
async def test_new_verbs_reject_another_users_search(tmp_path, verb):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    ctx.subscriptions.register(222)
    search_id = ctx.subscriptions.add_search(222, "theirs", "https://example.com/s")

    result = await dispatch_callback(f"{verb}:{search_id}", 111, ctx)

    assert "no longer exists" in result.text.lower()
    assert result.followup is None
    assert ctx.subscriptions.get_search_by_id(222, search_id).paused is False


@pytest.mark.asyncio
async def test_li_answers_fast_and_sends_listings_in_the_followup(tmp_path):
    ctx, search_id = _registered_ctx_with_search(tmp_path)
    ctx.notifier = RecordingNotifier()

    async def fetch_html(url):
        return LISTING_HTML

    ctx.fetch_html = fetch_html

    result = await dispatch_callback(f"li:{search_id}", 111, ctx)

    assert "fetching" in result.text.lower()
    assert result.reply_markup["inline_keyboard"][0][0]["callback_data"] == f"sp:{search_id}"
    assert ctx.notifier.sent == []  # nothing sent until the followup runs
    await result.followup()
    assert "Item One" in ctx.notifier.sent[-1][1]


@pytest.mark.asyncio
async def test_li_followup_reports_a_fetch_failure(tmp_path):
    ctx, search_id = _registered_ctx_with_search(tmp_path)
    ctx.notifier = RecordingNotifier()

    async def fetch_html(url):
        raise RuntimeError("browser died")

    ctx.fetch_html = fetch_html

    result = await dispatch_callback(f"li:{search_id}", 111, ctx)
    await result.followup()  # must not raise

    assert "couldn't fetch listings for 'speediance'" in ctx.notifier.sent[-1][1].lower()


@pytest.mark.asyncio
async def test_ms_sends_the_panel_as_a_new_message(tmp_path):
    ctx, search_id = _registered_ctx_with_search(tmp_path)

    result = await dispatch_callback(f"ms:{search_id}", 111, ctx)

    assert result.send_as_new is True
    assert "speediance" in result.text
    flat = [b["callback_data"] for row in result.reply_markup["inline_keyboard"] for b in row]
    assert f"tg:{search_id}:pa" in flat


@pytest.mark.asyncio
async def test_cmt_toggles_mute_and_relabels_without_dropping_the_url(tmp_path):
    ctx, search_id = _registered_ctx_with_search(tmp_path)
    card = {
        "inline_keyboard": [
            [{"text": "Open listing", "url": "https://www.carousell.sg/p/x-1/"}],
            [
                {"text": "🔇 Mute this search", "callback_data": f"cmt:{search_id}"},
                {"text": "👁 Show bumped", "callback_data": f"cbb:{search_id}"},
            ],
            [{"text": "⚙️ Manage search", "callback_data": f"ms:{search_id}"}],
        ]
    }

    muted = await dispatch_callback(f"cmt:{search_id}", 111, ctx, card)
    assert ctx.subscriptions.get_search_by_id(111, search_id).paused is True
    assert muted.toast == "Muted 'speediance'."
    assert muted.reply_markup["inline_keyboard"][0][0]["url"] == "https://www.carousell.sg/p/x-1/"
    assert muted.reply_markup["inline_keyboard"][1][0]["text"] == "🔔 Unmute"
    assert card["inline_keyboard"][1][0]["text"] == "🔇 Mute this search"  # caller's dict untouched

    unmuted = await dispatch_callback(f"cmt:{search_id}", 111, ctx, muted.reply_markup)
    assert ctx.subscriptions.get_search_by_id(111, search_id).paused is False
    assert unmuted.toast == "Unmuted 'speediance'."
    assert unmuted.reply_markup["inline_keyboard"][1][0]["text"] == "🔇 Mute this search"


@pytest.mark.asyncio
async def test_cmt_without_current_markup_builds_a_keyboard_with_no_empty_url(tmp_path):
    ctx, search_id = _registered_ctx_with_search(tmp_path)

    result = await dispatch_callback(f"cmt:{search_id}", 111, ctx)

    flat = [b for row in result.reply_markup["inline_keyboard"] for b in row]
    assert all("url" not in b for b in flat)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_callbacks.py -q`
Expected: FAIL — new verbs fall through to "Unknown action".

- [ ] **Step 3: Implement**

In `src/carouauto/commands.py`, change the subscriptions import to `from .subscriptions import SubscriptionStore, UserSearch` and add above `handle_list`:

```python
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
```

Replace `handle_list`'s body after the `sub is None` check with `return await current_listings_text(sub, ctx)`.

In `src/carouauto/callbacks.py`:
- Change the commands import to also bring in `current_listings_text`.
- Replace `_card_keyboard_with_updated_bumped_label` with a general relabel helper:

```python
def _relabel_button(existing_markup: dict, callback_data: str, new_text: str) -> dict:
    # Copies every button so the caller's dict (parsed from the incoming
    # update) is never mutated, and keeps URL buttons exactly as they were.
    rows = [[dict(button) for button in row] for row in existing_markup["inline_keyboard"]]
    for row in rows:
        for button in row:
            if button.get("callback_data") == callback_data:
                button["text"] = new_text
    return {"inline_keyboard": rows}
```

  and in the `cbb` branch replace the helper call with `_relabel_button(current_markup, f"cbb:{updated.search_id}", ui.bumped_label(updated.hide_bumped))`.
- Add a module-level table after `_relabel_button`:

```python
# Explicit setters used by the pickers: tap "pa" always pauses (never
# toggles), and the Undo button points at the opposite verb.
_STATE_VERBS = {
    "pa": ("paused", True, "Paused '{name}'.", "re"),
    "re": ("paused", False, "Resumed '{name}'.", "pa"),
    "hb": ("hide_bumped", True, "Hiding bumped listings for '{name}'.", "sb"),
    "sb": ("hide_bumped", False, "Showing bumped listings for '{name}' again.", "hb"),
}
```

- Inside `dispatch_callback`'s `try`, after the `rmn` branch, add:

```python
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
                return _stale_search_result()
            return CallbackResult(
                text=ui.search_panel_text(sub, ctx.states.get(sub.url)),
                reply_markup=ui.search_panel_keyboard(sub),
                send_as_new=True,
            )
```

- Replace the `cmt` branch:

```python
        if verb == "cmt":
            sub = ctx.subscriptions.get_search_by_id(chat_id, int(parts[1]))
            if sub is None:
                return _stale_search_result()
            now_paused = not sub.paused
            ctx.subscriptions.set_paused(chat_id, sub.name, now_paused)
            if current_markup is None:
                markup = ui.notification_card_keyboard(sub.search_id, "", sub.hide_bumped, now_paused)
            else:
                markup = _relabel_button(current_markup, f"cmt:{sub.search_id}", ui.mute_label(now_paused))
            toast = f"Muted '{sub.name}'." if now_paused else f"Unmuted '{sub.name}'."
            return CallbackResult(text=CARD_TEXT_UNCHANGED, reply_markup=markup, toast=toast)
```

- In the `cbb` branch's `current_markup is None` path, the existing `ui.notification_card_keyboard(updated.search_id, "", updated.hide_bumped)` call now produces no URL row — keep it, but pass `updated.paused` as the fourth argument.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (existing `cmt`/`cbb` tests and the photo-card listener test still pass; `/list` tests unchanged).

- [ ] **Step 5: Commit**

```bash
git add src/carouauto/commands.py src/carouauto/callbacks.py tests/test_callbacks.py
git commit -m "$(cat <<'EOF'
feat: explicit pause/resume/bumped verbs with undo, current listings, card manage + unmute

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Every add path shows the new search's panel

**Files:**
- Modify: `src/carouauto/commands.py` (`_finish_add` returns `(text, markup)`; `_reply_with`; `handle_add`, `handle_addurl`)
- Modify: `src/carouauto/callbacks.py` (`afy`)
- Modify: `src/carouauto/telegram_listener.py` (`_apply_pending_add_price`)
- Test: `tests/test_commands.py`, `tests/test_callbacks.py`, `tests/test_telegram_listener.py`

**Interfaces:**
- Produces: `commands._finish_add(chat_id, name, url, min_price, max_price, ctx) -> tuple[str, dict | None]` — duplicate name → `(error_text, None)`; success → `(header + "\n\n" + panel_text, panel_keyboard)`. `commands._reply_with(chat_id, ctx, text, markup) -> str` — returns `text` when `markup is None`, else sends and returns `""`.

**Behavior change:** successful typed adds return `""` and send the panel. `test_commands.py`'s `make_ctx` uses `notifier=None`, and many tests call `handle_addurl` as setup — so this task switches `make_ctx` to a recording notifier first.

- [ ] **Step 1: Switch `make_ctx` to a recording notifier**

At the top of `tests/test_commands.py` (after the imports), add:

```python
class RecordingNotifier:
    def __init__(self):
        self.sent = []
        self.documents = []

    def send_text(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text, reply_markup))

    def send_document(self, chat_id, file_path, filename):
        self.documents.append((chat_id, filename))
```

and change `make_ctx`'s `notifier=None,` to `notifier=RecordingNotifier(),`. Tests that assign their own `ctx.notifier = FakeNotifier()` keep working. Run `.venv/bin/python -m pytest tests/test_commands.py -q` — expected: PASS (no behavior changed yet).

- [ ] **Step 2: Write the failing tests**

In `tests/test_commands.py`, replace these existing tests' bodies (they asserted on the returned success string):

```python
@pytest.mark.asyncio
async def test_handle_addurl_creates_a_search(tmp_path):
    ctx = make_ctx(tmp_path)

    reply = await handle_addurl(["speediance", "https://example.com/s"], 111, ctx)

    assert reply == ""
    chat_id, text, markup = ctx.notifier.sent[-1]
    assert text.startswith("Added 'speediance'.")
    found = ctx.subscriptions.get_search(111, "speediance")
    assert found.url == "https://example.com/s"
    assert found.min_price is None


@pytest.mark.asyncio
async def test_handle_add_with_single_arg_uses_it_as_both_name_and_query(tmp_path):
    ctx = make_ctx(tmp_path)

    reply = await handle_add(["speediance"], 111, ctx)

    assert reply == ""
    assert "Added 'speediance'." in ctx.notifier.sent[-1][1]
    found = ctx.subscriptions.get_search(111, "speediance")
    assert found is not None
    assert found.url == "https://www.carousell.sg/search/speediance?sort_by=3"


@pytest.mark.asyncio
async def test_new_searches_hide_bumped_by_default(tmp_path):
    ctx = make_ctx(tmp_path)

    await handle_addurl(["speediance", "https://example.com/s"], 111, ctx)

    found = ctx.subscriptions.get_search(111, "speediance")
    assert found.hide_bumped is True
    text, markup = ctx.notifier.sent[-1][1], ctx.notifier.sent[-1][2]
    assert "hidden by default" in text
    by_data = {b["callback_data"]: b["text"] for row in markup["inline_keyboard"] for b in row}
    assert by_data[f"tg:{found.search_id}:hb"] == "👁 Show bumped"


@pytest.mark.asyncio
async def test_dispatch_allows_registered_user_to_add(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)

    reply = await dispatch("add", ["speediance", "https://example.com"], 111, ctx)

    assert reply == ""
    assert "added" in ctx.notifier.sent[-1][1].lower()
```

Add a new test:

```python
@pytest.mark.asyncio
async def test_added_panel_has_the_poll_interval_and_a_current_listings_button(tmp_path):
    ctx = make_ctx(tmp_path)

    await handle_add(["speediance"], 111, ctx)

    _, text, markup = ctx.notifier.sent[-1]
    assert "every ~5 minutes" in text
    flat = [b["callback_data"] for row in markup["inline_keyboard"] for b in row]
    search_id = ctx.subscriptions.get_search(111, "speediance").search_id
    assert f"li:{search_id}" in flat
```

The duplicate-name tests (`..._duplicate_name_fails_clearly`) keep asserting `"already" in reply.lower()` — the error is still returned as a string.

In `tests/test_callbacks.py`, extend `test_afy_creates_the_search_from_the_pending_query` with:

```python
    flat = [b["callback_data"] for row in result.reply_markup["inline_keyboard"] for b in row]
    assert any(d.startswith("tg:") for d in flat)
```

In `tests/test_telegram_listener.py`, extend `test_a_reply_while_pending_add_price_finishes_the_add` with:

```python
    _, text, markup = ctx.notifier.sent[-1]
    assert text.startswith("Added 'speediance'.")
    assert markup is not None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest -q`
Expected: FAIL — adds still return the old text; `result.reply_markup` is `None` for `afy`.

- [ ] **Step 4: Implement**

In `src/carouauto/commands.py` replace `_finish_add` and add `_reply_with`:

```python
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
```

In `handle_add` and `handle_addurl`, replace the final `return _finish_add(...)` with:

```python
    text, markup = _finish_add(chat_id, name, url, min_price, max_price, ctx)
    return _reply_with(chat_id, ctx, text, markup)
```

In `src/carouauto/callbacks.py` `afy`, replace the last two lines with:

```python
            text, markup = _finish_add(chat_id, query, url, None, None, ctx)
            return CallbackResult(text=text, reply_markup=markup)
```

In `src/carouauto/telegram_listener.py` `_apply_pending_add_price`, replace the last two lines with:

```python
    text, markup = _finish_add(chat_id, pending.query, url, min_price, max_price, ctx)
    ctx.notifier.send_text(chat_id, text, reply_markup=markup)
```

Run `grep -rn "_finish_add(" src` and confirm exactly these four call sites exist, all unpacking a tuple.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/carouauto/commands.py src/carouauto/callbacks.py src/carouauto/telegram_listener.py tests/test_commands.py tests/test_callbacks.py tests/test_telegram_listener.py
git commit -m "$(cat <<'EOF'
feat: show the new search's panel after every add path

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Pickers for `/remove`, `/pause`, `/resume`, `/hidebumped`, `/showbumped`, `/list`

**Files:**
- Modify: `src/carouauto/commands.py`
- Test: `tests/test_commands.py`

**Interfaces:**
- Consumes: `ui.search_picker_keyboard` (Task 2); verbs `rm`, `pa`, `re`, `hb`, `sb`, `li` (existing + Task 4); `current_listings_text` (Task 4).
- Produces: `commands._send_picker(chat_id: int, ctx: BotContext, verb: str, missing_name: str | None) -> str`. For these six commands the name is `" ".join(args)`, so unquoted multi-word names (`/pause nintendo switch`) work.

- [ ] **Step 1: Write the failing tests**

In `tests/test_commands.py`, replace:

```python
@pytest.mark.asyncio
async def test_handle_remove_unknown_search(tmp_path):
    ctx = make_ctx(tmp_path)

    reply = await handle_remove(["does-not-exist"], 111, ctx)

    assert "no tracked searches" in reply.lower()  # nothing to pick from


@pytest.mark.asyncio
async def test_handle_hidebumped_unknown_search(tmp_path):
    ctx = make_ctx(tmp_path)

    reply = await handle_hidebumped(["nope"], 111, ctx)

    assert "no tracked searches" in reply.lower()


@pytest.mark.asyncio
async def test_handle_showbumped_with_no_name_and_no_searches(tmp_path):
    ctx = make_ctx(tmp_path)

    reply = await handle_showbumped([], 111, ctx)

    assert "no tracked searches" in reply.lower()
```

(the last one replaces `test_handle_showbumped_wrong_arg_count_gives_usage`), and change `test_handle_list_unknown_search`'s assertion to `assert "no tracked searches" in reply.lower()`.

Add:

```python
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler_name, verb",
    [
        ("handle_remove", "rm"),
        ("handle_pause", "pa"),
        ("handle_resume", "re"),
        ("handle_hidebumped", "hb"),
        ("handle_showbumped", "sb"),
        ("handle_list", "li"),
    ],
)
async def test_name_commands_without_a_name_send_a_picker(tmp_path, handler_name, verb):
    import carouauto.commands as commands_module

    ctx = make_ctx(tmp_path)
    await handle_addurl(["speediance", "https://example.com/s"], 111, ctx)
    search_id = ctx.subscriptions.get_search(111, "speediance").search_id

    reply = await getattr(commands_module, handler_name)([], 111, ctx)

    assert reply == ""
    _, text, markup = ctx.notifier.sent[-1]
    assert text == "Which search?"
    assert markup["inline_keyboard"] == [[{"text": "speediance", "callback_data": f"{verb}:{search_id}"}]]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler_name, verb",
    [
        ("handle_remove", "rm"),
        ("handle_pause", "pa"),
        ("handle_resume", "re"),
        ("handle_hidebumped", "hb"),
        ("handle_showbumped", "sb"),
        ("handle_list", "li"),
    ],
)
async def test_name_commands_with_an_unknown_name_send_a_picker(tmp_path, handler_name, verb):
    import carouauto.commands as commands_module

    ctx = make_ctx(tmp_path)
    await handle_addurl(["speediance", "https://example.com/s"], 111, ctx)

    reply = await getattr(commands_module, handler_name)(["nope"], 111, ctx)

    assert reply == ""
    _, text, markup = ctx.notifier.sent[-1]
    assert text == "No search named 'nope' — pick one:"
    assert markup["inline_keyboard"][0][0]["callback_data"].startswith(f"{verb}:")


@pytest.mark.asyncio
async def test_case_mismatched_name_shows_the_picker_and_changes_nothing(tmp_path):
    ctx = make_ctx(tmp_path)
    await handle_addurl(["speediance", "https://example.com/s"], 111, ctx)

    reply = await handle_pause(["Speediance"], 111, ctx)

    assert reply == ""
    assert ctx.notifier.sent[-1][1] == "No search named 'Speediance' — pick one:"
    assert ctx.subscriptions.get_search(111, "speediance").paused is False


@pytest.mark.asyncio
async def test_unquoted_multi_word_name_is_joined(tmp_path):
    ctx = make_ctx(tmp_path)
    await handle_addurl(["nintendo switch", "https://example.com/s"], 111, ctx)

    reply = await handle_pause(["nintendo", "switch"], 111, ctx)

    assert reply == "Paused 'nintendo switch'."
    assert ctx.subscriptions.get_search(111, "nintendo switch").paused is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_commands.py -q`
Expected: FAIL — handlers still return usage / "No search named" strings.

- [ ] **Step 3: Implement**

In `src/carouauto/commands.py` add after `_reply_with`:

```python
def _send_picker(chat_id: int, ctx: BotContext, verb: str, missing_name: str | None) -> str:
    subs = ctx.subscriptions.list_searches(chat_id)
    if not subs:
        return "You have no tracked searches yet. Use /add <name> to start one."
    prompt = "Which search?" if missing_name is None else f"No search named '{missing_name}' — pick one:"
    ctx.notifier.send_text(chat_id, prompt, reply_markup=ui.search_picker_keyboard(subs, verb))
    return ""
```

Replace the six handlers:

```python
async def handle_remove(args: list[str], chat_id: int, ctx: BotContext) -> str:
    if not args:
        return _send_picker(chat_id, ctx, "rm", None)
    name = " ".join(args)
    search_id = ctx.subscriptions.remove_search(chat_id, name)
    if search_id is None:
        return _send_picker(chat_id, ctx, "rm", name)
    ctx.seen_store.delete_all_for_search(search_id)
    return f"Removed '{name}'."


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


async def handle_list(args: list[str], chat_id: int, ctx: BotContext) -> str:
    if not args:
        return _send_picker(chat_id, ctx, "li", None)
    name = " ".join(args)
    sub = ctx.subscriptions.get_search(chat_id, name)
    if sub is None:
        return _send_picker(chat_id, ctx, "li", name)
    return await current_listings_text(sub, ctx)
```

In `handle_help`'s text, add one line after the `/list` line:

```python
        "Commands that take a <name> show your searches as buttons if you leave it out.\n"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/carouauto/commands.py tests/test_commands.py
git commit -m "$(cat <<'EOF'
feat: search pickers for remove, pause, resume, hide/show bumped and list

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Pickers and name-only prompts for `/setprice`, `/setexclude`, `/setcondition`

**Files:**
- Modify: `src/carouauto/commands.py`
- Test: `tests/test_commands.py`

**Interfaces:**
- Consumes: `_send_picker` (Task 6); `ui.price_prompt`, `ui.exclude_prompt` (Task 2); `ui.condition_keyboard`; verbs `pp`, `px`, `cd`.
- Produces: `commands._start_prompt(chat_id: int, ctx: BotContext, sub: UserSearch, kind: str) -> str` (`kind` is `"setprice"` or `"setexclude"`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_commands.py`:

```python
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler_name, verb",
    [("handle_setprice", "pp"), ("handle_setexclude", "px"), ("handle_setcondition", "cd")],
)
async def test_filter_commands_without_a_name_send_a_picker(tmp_path, handler_name, verb):
    import carouauto.commands as commands_module

    ctx = make_ctx(tmp_path)
    await handle_addurl(["speediance", "https://example.com/s"], 111, ctx)
    search_id = ctx.subscriptions.get_search(111, "speediance").search_id

    reply = await getattr(commands_module, handler_name)([], 111, ctx)

    assert reply == ""
    assert ctx.notifier.sent[-1][2]["inline_keyboard"][0][0]["callback_data"] == f"{verb}:{search_id}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler_name, args, verb",
    [
        ("handle_setprice", ["nope", "10", "20"], "pp"),
        ("handle_setexclude", ["nope", "case"], "px"),
        ("handle_setcondition", ["nope", "Brand", "new"], "cd"),
    ],
)
async def test_filter_commands_with_an_unknown_name_send_a_picker(tmp_path, handler_name, args, verb):
    import carouauto.commands as commands_module

    ctx = make_ctx(tmp_path)
    await handle_addurl(["speediance", "https://example.com/s"], 111, ctx)

    reply = await getattr(commands_module, handler_name)(args, 111, ctx)

    assert reply == ""
    _, text, markup = ctx.notifier.sent[-1]
    assert text == "No search named 'nope' — pick one:"
    assert markup["inline_keyboard"][0][0]["callback_data"].startswith(f"{verb}:")


@pytest.mark.asyncio
async def test_setprice_with_only_a_name_starts_the_price_prompt(tmp_path):
    ctx = make_ctx(tmp_path)
    await handle_addurl(["speediance", "https://example.com/s"], 111, ctx)
    search_id = ctx.subscriptions.get_search(111, "speediance").search_id

    reply = await handle_setprice(["speediance"], 111, ctx)

    assert reply == ""
    _, text, markup = ctx.notifier.sent[-1]
    assert "min and max for 'speediance'" in text
    assert markup == {"force_reply": True}
    assert ctx.pending[111].kind == "setprice"
    assert ctx.pending[111].search_id == search_id


@pytest.mark.asyncio
async def test_setexclude_with_only_a_name_starts_the_exclude_prompt(tmp_path):
    ctx = make_ctx(tmp_path)
    await handle_addurl(["speediance", "https://example.com/s"], 111, ctx)

    reply = await handle_setexclude(["speediance"], 111, ctx)

    assert reply == ""
    assert ctx.pending[111].kind == "setexclude"
    assert ctx.notifier.sent[-1][2] == {"force_reply": True}


@pytest.mark.asyncio
async def test_setcondition_with_only_a_name_sends_the_condition_picker(tmp_path):
    ctx = make_ctx(tmp_path)
    await handle_addurl(["speediance", "https://example.com/s"], 111, ctx)
    search_id = ctx.subscriptions.get_search(111, "speediance").search_id

    reply = await handle_setcondition(["speediance"], 111, ctx)

    assert reply == ""
    _, text, markup = ctx.notifier.sent[-1]
    assert text == "Set condition for 'speediance':"
    flat = [b["callback_data"] for row in markup["inline_keyboard"] for b in row]
    assert f"cds:{search_id}:0" in flat
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_commands.py -q`
Expected: FAIL — handlers still return usage strings.

- [ ] **Step 3: Implement**

In `src/carouauto/commands.py` add after `_send_picker`:

```python
def _start_prompt(chat_id: int, ctx: BotContext, sub: UserSearch, kind: str) -> str:
    ctx.pending[chat_id] = PendingInput(kind=kind, search_id=sub.search_id, created_at=datetime.now(timezone.utc))
    prompt = ui.price_prompt(sub.name) if kind == "setprice" else ui.exclude_prompt(sub.name)
    ctx.notifier.send_text(chat_id, prompt, reply_markup={"force_reply": True})
    return ""
```

Replace the three handlers (the name is `args[0]` — values follow it, so multi-word names still need quotes here, as today):

```python
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
```

In `src/carouauto/callbacks.py`, the `cd` branch's text is `f"Set condition for '{sub.name}':"` — identical, so typed and tapped paths match.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (existing full-argument `setprice`/`setexclude`/`setcondition` tests unchanged).

- [ ] **Step 5: Commit**

```bash
git add src/carouauto/commands.py tests/test_commands.py
git commit -m "$(cat <<'EOF'
feat: pickers and name-only prompts for setprice, setexclude, setcondition

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Always-visible bottom keyboard

**Files:**
- Modify: `src/carouauto/commands.py` (`handle_start`, `handle_register`, `handle_help`)
- Modify: `src/carouauto/telegram_listener.py` (menu-button routing)
- Test: `tests/test_commands.py`, `tests/test_telegram_listener.py`

**Interfaces:**
- Consumes: `ui.bottom_keyboard`, `ui.MENU_BUTTONS` (Task 2); `dispatch` (existing).

- [ ] **Step 1: Write the failing tests**

In `tests/test_commands.py`, replace `test_handle_start_sends_the_main_menu` and `test_handle_register_with_correct_password_succeeds` and `test_handle_help_sends_the_main_menu` with:

```python
@pytest.mark.asyncio
async def test_handle_start_for_an_unregistered_user_says_register_and_has_no_keyboard(tmp_path):
    ctx = make_ctx(tmp_path)

    reply = await handle_start([], 111, ctx)

    assert reply == ""
    _, text, markup = ctx.notifier.sent[-1]
    assert "/register" in text
    assert markup is None


@pytest.mark.asyncio
async def test_handle_start_for_a_registered_user_sends_the_bottom_keyboard(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)

    reply = await handle_start([], 111, ctx)

    assert reply == ""
    assert ctx.notifier.sent[-1][2]["keyboard"][0][0]["text"] == "🔎 My searches"


@pytest.mark.asyncio
async def test_handle_register_with_correct_password_sends_the_bottom_keyboard(tmp_path):
    ctx = make_ctx(tmp_path)

    reply = await handle_register([REGISTRATION_PASSWORD], 111, ctx)

    assert ctx.subscriptions.is_active(111) is True
    assert reply == ""
    _, text, markup = ctx.notifier.sent[-1]
    assert "registered" in text.lower()
    assert markup["is_persistent"] is True


@pytest.mark.asyncio
async def test_handle_help_for_an_unregistered_user_sends_only_the_help(tmp_path):
    ctx = make_ctx(tmp_path)

    reply = await handle_help([], 111, ctx)

    assert reply == ""
    assert len(ctx.notifier.sent) == 1
    _, text, markup = ctx.notifier.sent[0]
    assert "5 minute" in text.lower()
    assert markup["inline_keyboard"][0][0]["callback_data"] == "mm:se"


@pytest.mark.asyncio
async def test_handle_help_for_a_registered_user_also_turns_on_the_bottom_keyboard(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)

    await handle_help([], 111, ctx)

    assert len(ctx.notifier.sent) == 2
    _, text, markup = ctx.notifier.sent[1]
    assert text == "Menu buttons are below the chat box."
    assert markup["is_persistent"] is True
```

Add to `tests/test_telegram_listener.py`:

```python
def _text_update(text, chat_id=111):
    return {"update_id": 5, "message": {"chat": {"id": chat_id}, "text": text}}


@pytest.mark.asyncio
async def test_my_searches_button_routes_to_searches(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    search_id = ctx.subscriptions.add_search(111, "speediance", "https://example.com/s")

    await handle_update(_text_update("🔎 My searches"), ctx)

    _, text, markup = ctx.notifier.sent[-1]
    assert markup["inline_keyboard"][0][0]["callback_data"] == f"sp:{search_id}"


@pytest.mark.asyncio
async def test_my_searches_button_with_no_searches_says_so(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)

    await handle_update(_text_update("🔎 My searches"), ctx)

    assert "no tracked searches" in ctx.notifier.sent[-1][1].lower()


@pytest.mark.asyncio
async def test_add_search_button_starts_the_guided_add(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)

    await handle_update(_text_update("➕ Add search"), ctx)

    assert ctx.pending[111].kind == "add_query"
    assert ctx.notifier.sent[-1][2] == {"force_reply": True}


@pytest.mark.asyncio
async def test_status_button_replies_with_status(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    ctx.subscriptions.add_search(111, "speediance", "https://example.com/s")

    await handle_update(_text_update("📊 Status"), ctx)

    assert "speediance: not yet checked" in ctx.notifier.sent[-1][1]


@pytest.mark.asyncio
async def test_menu_button_beats_a_pending_prompt_and_clears_it(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    search_id = ctx.subscriptions.add_search(111, "speediance", "https://example.com/s")
    ctx.pending[111] = PendingInput(kind="setprice", search_id=search_id, created_at=datetime.now(timezone.utc))

    await handle_update(_text_update("📊 Status"), ctx)

    assert 111 not in ctx.pending
    found = ctx.subscriptions.get_search_by_id(111, search_id)
    assert (found.min_price, found.max_price) == (None, None)
    assert "speediance" in ctx.notifier.sent[-1][1]


@pytest.mark.asyncio
async def test_revoked_user_tapping_a_menu_button_is_told_to_register(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    ctx.subscriptions.revoke(111)

    await handle_update(_text_update("🔎 My searches"), ctx)

    assert ctx.notifier.sent[-1][1] == "You need to /register first."
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_commands.py tests/test_telegram_listener.py -q`
Expected: FAIL — `/start` still sends the inline menu; menu labels are ignored as plain text.

- [ ] **Step 3: Implement**

In `src/carouauto/commands.py` replace `handle_start` and `handle_register`:

```python
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
```

At the end of `handle_help`, replace `ctx.notifier.send_text(chat_id, text, reply_markup=ui.main_menu_keyboard())` / `return ""` with:

```python
    ctx.notifier.send_text(chat_id, text, reply_markup=ui.main_menu_keyboard())
    # A message carries one reply_markup, so the persistent bottom keyboard
    # needs its own message.
    if ctx.subscriptions.is_active(chat_id):
        ctx.notifier.send_text(chat_id, "Menu buttons are below the chat box.", reply_markup=ui.bottom_keyboard())
    return ""
```

In `src/carouauto/telegram_listener.py` `handle_update`, right after `text = message["text"]` and before the pending check, add:

```python
        # Bottom-keyboard taps arrive as plain text. They win over any pending
        # prompt (the user navigated away) and go through dispatch so the
        # normal registration gate applies.
        menu_command = ui.MENU_BUTTONS.get(text.strip())
        if menu_command is not None:
            ctx.pending.pop(chat_id, None)
            reply = await dispatch(menu_command, [], chat_id, ctx)
            if reply:
                ctx.notifier.send_text(chat_id, reply)
            return
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS — including `test_handle_update_routes_a_plain_command` (`/start` for an unregistered chat still mentions register) and `test_dispatch_allows_public_commands_without_registration`.

- [ ] **Step 5: Commit**

```bash
git add src/carouauto/commands.py src/carouauto/telegram_listener.py tests/test_commands.py tests/test_telegram_listener.py
git commit -m "$(cat <<'EOF'
feat: always-visible bottom keyboard for registered users

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Verify, push, deploy to the VPS

Run by the controller after the final whole-branch review is clean — not by a task subagent.

- [ ] **Step 1: Full suite**

Run: `.venv/bin/python -m pytest -q` — expected: all green.

- [ ] **Step 2: Push**

```bash
gh auth switch --hostname github.com --user ststheting
git push origin main
gh auth switch --hostname github.com --user hmishaun
git log --oneline -1 origin/main
```

Expected: `origin/main` equals local `HEAD`; `gh auth status` shows `hmishaun` active.

- [ ] **Step 3: Deploy**

```bash
ssh -i ~/.ssh/carouauto_vultr root@139.180.213.97 "cd /opt/carouauto && sudo -u carouauto git pull origin main && systemctl restart carouauto && sleep 5 && systemctl is-active carouauto"
```

Expected: fast-forward pull, then `active`.

- [ ] **Step 4: Check logs**

```bash
sleep 30; ssh -i ~/.ssh/carouauto_vultr root@139.180.213.97 "cd /opt/carouauto && git log --oneline -1 && journalctl -u carouauto --since '1 minute ago' --no-pager"
```

Expected: VPS HEAD matches `origin/main`; at least one `polled` line; no tracebacks.

- [ ] **Step 5: Hand the user a live checklist**

Ask the user to try in Telegram: `/start` (bottom keyboard appears), each bottom button, `/pause` with no name → pick → Undo, `/remove` with no name → pick → confirm, `/setprice speediance` → prompt, a panel's 👀 Current listings, a card's ⚙️ Manage search and Mute → Unmute. And to report whether the bottom keyboard stays visible while a ForceReply prompt is open (the spec's one unverified Telegram behavior).
