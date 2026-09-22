# Telegram Button UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Telegram bot usable through inline-keyboard buttons and ForceReply prompts instead of typed commands with quoted names and positional arguments, while every typed command keeps working unchanged as a fallback.

**Architecture:** A new `callbacks.py` routes `callback_query` updates the same way `commands.dispatch` routes `message` updates, producing a `CallbackResult` (new message text + keyboard + optional toast) that the listener applies via `edit_message`/`answer_callback`. A new `ui.py` holds pure functions that build keyboards and panel text — no I/O, trivially unit tested. Handlers that need to *send* something beyond their return string (a keyboard-bearing message, a ForceReply prompt) call `ctx.notifier` directly and return `""`, exactly like `handle_backup` already does for its document send — no change to `dispatch()`'s `str` return type, so the ~80 existing `test_commands.py` tests are untouched except the handful whose *behavior* is intentionally changing (documented per-task below).

**Tech Stack:** Python 3.11+, httpx (sync client in `notifier.py`), pytest + pytest-asyncio. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-21-telegram-button-ux-design.md`

## Global Constraints

- Callback data must be ≤ 64 bytes (Telegram's hard limit) — every keyboard-building function is covered by a test asserting this.
- The bot token must never appear in a log line, exception message, or chained exception (`__cause__`/`__context__`) — same rule as every existing `notifier.py`/`telegram_listener.py` method; new methods follow the identical pattern (`type(exc).__name__` only).
- `dispatch_callback` never raises to the listener loop, and the listener always calls `answer_callback` even when something upstream failed — the button's loading spinner must always stop.
- No database schema changes — everything uses existing `UserSearch` columns.
- Every task's tests run with `.venv/bin/python -m pytest -q` and must pass before the task's commit.
- GitHub pushes: `gh auth switch --hostname github.com --user ststheting` before `git push`, then `gh auth switch --hostname github.com --user hmishaun` after — every single push, no exceptions.

---

## File Structure

- **Create** `src/carouauto/ui.py` — pure keyboard/text builders. No imports from `notifier.py`, `callbacks.py`, or `telegram_listener.py` (one-directional dependency: `callbacks.py` and `commands.py` import `ui.py`, never the reverse).
- **Create** `src/carouauto/callbacks.py` — `CallbackResult` dataclass, `PendingInput` dataclass, `dispatch_callback()`. Imports `BotContext` from `commands.py` and builders from `ui.py`.
- **Create** `tests/test_ui.py`, `tests/test_callbacks.py`.
- **Modify** `src/carouauto/notifier.py` — `reply_markup` param on `send_text`; new `edit_message`, `answer_callback`, `send_photo`.
- **Modify** `src/carouauto/subscriptions.py` — new `get_search_by_id`.
- **Modify** `src/carouauto/commands.py` — `BotContext` gains `pending`; `handle_searches`, `handle_start`, `handle_help`, `handle_add` (no-args case) send keyboards/prompts directly via `ctx.notifier` and return `""`.
- **Modify** `src/carouauto/telegram_listener.py` — extract `handle_update()`; route `callback_query` updates and pending-input text replies; `allowed_updates`; admin command scope.
- **Modify** `src/carouauto/scheduler.py` — `send_new_listings` call site passes `sub.search_id`.
- **Modify** `tests/test_commands.py`, `tests/test_scheduler.py`, `tests/test_notifier.py`, `tests/test_telegram_listener.py` (new file) as each task requires.

---

## Layer 0: Foundation

### Task 1: `ui.py` — generic keyboard builder

**Files:**
- Create: `src/carouauto/ui.py`
- Test: `tests/test_ui.py`

**Interfaces:**
- Produces: `ui.MAX_CALLBACK_DATA_BYTES: int`, `ui.inline_keyboard(rows: list[list[tuple[str, str]]]) -> dict` — each tuple is `(button_text, callback_data)`; returns a Telegram `InlineKeyboardMarkup`-shaped dict `{"inline_keyboard": [[{"text": ..., "callback_data": ...}, ...], ...]}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ui.py
from carouauto import ui


def test_inline_keyboard_builds_telegram_shaped_markup():
    markup = ui.inline_keyboard([[("Pause", "tg:1:pa"), ("Back", "ls")], [("Remove", "rm:1")]])

    assert markup == {
        "inline_keyboard": [
            [
                {"text": "Pause", "callback_data": "tg:1:pa"},
                {"text": "Back", "callback_data": "ls"},
            ],
            [{"text": "Remove", "callback_data": "rm:1"}],
        ]
    }


def test_inline_keyboard_rejects_callback_data_over_the_telegram_limit():
    too_long = "x" * (ui.MAX_CALLBACK_DATA_BYTES + 1)

    try:
        ui.inline_keyboard([[("Button", too_long)]])
        assert False, "expected a ValueError"
    except ValueError as exc:
        assert "64" in str(exc)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_ui.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'carouauto.ui'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/carouauto/ui.py
from __future__ import annotations

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_ui.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/carouauto/ui.py tests/test_ui.py
git commit -m "feat: add ui.py keyboard builder with callback_data length guard"
```

---

### Task 2: `TelegramNotifier` — keyboards, edit, answer callback

**Files:**
- Modify: `src/carouauto/notifier.py`
- Test: `tests/test_notifier.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `TelegramNotifier.send_text(chat_id, text, reply_markup=None)`, `TelegramNotifier.edit_message(chat_id, message_id, text, reply_markup) -> None`, `TelegramNotifier.answer_callback(callback_query_id, text=None) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_notifier.py — add near the other send_text tests
import json


def test_send_text_includes_reply_markup_when_given():
    client = FakeClient()
    notifier = TelegramNotifier(BOT_TOKEN, client=client)
    markup = {"inline_keyboard": [[{"text": "Ok", "callback_data": "x"}]]}

    notifier.send_text(CHAT_ID, "hi", reply_markup=markup)

    _, data, _ = client.posts[0]
    assert json.loads(data["reply_markup"]) == markup


def test_send_text_omits_reply_markup_when_not_given():
    client = FakeClient()
    notifier = TelegramNotifier(BOT_TOKEN, client=client)

    notifier.send_text(CHAT_ID, "hi")

    _, data, _ = client.posts[0]
    assert "reply_markup" not in data


def test_edit_message_posts_new_text_and_markup():
    client = FakeClient()
    notifier = TelegramNotifier(BOT_TOKEN, client=client)
    markup = {"inline_keyboard": [[{"text": "Back", "callback_data": "ls"}]]}

    notifier.edit_message(CHAT_ID, 555, "updated text", markup)

    url, data, _ = client.posts[0]
    assert url == f"{TELEGRAM_API_BASE}/bot{BOT_TOKEN}/editMessageText"
    assert data["chat_id"] == CHAT_ID
    assert data["message_id"] == 555
    assert data["text"] == "updated text"
    assert json.loads(data["reply_markup"]) == markup


def test_edit_message_swallows_message_not_modified_error():
    class NotModifiedClient:
        def __init__(self):
            self.posts = []

        def post(self, url, data=None, files=None):
            self.posts.append((url, data, files))
            return httpx.Response(
                400,
                json={"ok": False, "description": "Bad Request: message is not modified"},
                request=httpx.Request("POST", url),
            )

    notifier = TelegramNotifier(BOT_TOKEN, client=NotModifiedClient())

    notifier.edit_message(CHAT_ID, 555, "same text", None)  # must not raise


def test_edit_message_raises_on_a_real_http_error_without_leaking_the_token():
    class FailingClient:
        def post(self, url, data=None, files=None):
            raise httpx.ConnectError(f"connection failed for {url}")

    notifier = TelegramNotifier(BOT_TOKEN, client=FailingClient())

    with pytest.raises(RuntimeError) as excinfo:
        notifier.edit_message(CHAT_ID, 555, "text", None)

    assert BOT_TOKEN not in str(excinfo.value)
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__context__ is None


def test_answer_callback_posts_the_callback_query_id():
    client = FakeClient()
    notifier = TelegramNotifier(BOT_TOKEN, client=client)

    notifier.answer_callback("cbq123", "Muted!")

    url, data, _ = client.posts[0]
    assert url == f"{TELEGRAM_API_BASE}/bot{BOT_TOKEN}/answerCallbackQuery"
    assert data == {"callback_query_id": "cbq123", "text": "Muted!"}


def test_answer_callback_without_text_omits_it():
    client = FakeClient()
    notifier = TelegramNotifier(BOT_TOKEN, client=client)

    notifier.answer_callback("cbq123")

    _, data, _ = client.posts[0]
    assert data == {"callback_query_id": "cbq123"}
```

Also add `TELEGRAM_API_BASE` to the test file's imports: `from carouauto.notifier import MAX_MESSAGE_CHARS, TELEGRAM_API_BASE, TelegramNotifier, format_message`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_notifier.py -v`
Expected: FAIL — `TypeError: send_text() got an unexpected keyword argument 'reply_markup'` and `AttributeError: 'TelegramNotifier' object has no attribute 'edit_message'` etc.

- [ ] **Step 3: Write minimal implementation**

```python
# src/carouauto/notifier.py — add near the top
import json
```

Replace `send_text` and add the three new methods (insert after `send_text`, before `send_document`):

```python
    def send_text(self, chat_id: int, text: str, reply_markup: dict | None = None) -> None:
        self._send_text(chat_id, text, reply_markup)

    def edit_message(
        self, chat_id: int, message_id: int, text: str, reply_markup: dict | None
    ) -> None:
        url = f"{TELEGRAM_API_BASE}/bot{self._bot_token}/editMessageText"
        data = {"chat_id": chat_id, "message_id": message_id, "text": text}
        if reply_markup is not None:
            data["reply_markup"] = json.dumps(reply_markup)
        try:
            response = self._client.post(url, data=data)
        except httpx.HTTPError as e:
            raise RuntimeError(f"Telegram edit failed: {type(e).__name__}")
        if response.status_code == 400 and "not modified" in response.text.lower():
            return  # editing to identical content — not a real failure
        try:
            response.raise_for_status()
        except httpx.HTTPError as e:
            raise RuntimeError(f"Telegram edit failed: {type(e).__name__}")

    def answer_callback(self, callback_query_id: str, text: str | None = None) -> None:
        url = f"{TELEGRAM_API_BASE}/bot{self._bot_token}/answerCallbackQuery"
        data = {"callback_query_id": callback_query_id}
        if text:
            data["text"] = text
        try:
            response = self._client.post(url, data=data)
            response.raise_for_status()
        except httpx.HTTPError as e:
            # Best-effort — the button's spinner just keeps spinning briefly
            # on the user's client if this fails; never worth crashing over.
            pass
```

Update `_send_text` to accept and forward `reply_markup`:

```python
    def _send_text(self, chat_id: int, text: str, reply_markup: dict | None = None) -> None:
        url = f"{TELEGRAM_API_BASE}/bot{self._bot_token}/sendMessage"
        failure_name = ""
        data = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            data["reply_markup"] = json.dumps(reply_markup)
        for attempt in range(2):  # one initial attempt plus one retry
            try:
                response = self._client.post(url, data=data)
                response.raise_for_status()
                return
            except httpx.HTTPError as e:
                # Only the exception's class name escapes. httpx's own message
                # embeds the request URL, which contains the bot token, so it
                # must never be interpolated, chained, or re-raised.
                failure_name = type(e).__name__
                if attempt == 0:
                    time.sleep(RETRY_BACKOFF_SECONDS)
        # Raised outside the except block so __context__ is not set either.
        raise RuntimeError(f"Telegram send failed after retry: {failure_name}")
```

`send_alert` and `send_new_listings` already call `self._send_text(chat_id, text)` positionally — unaffected by the new optional third parameter.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_notifier.py -v`
Expected: PASS (all tests, old and new)

- [ ] **Step 5: Commit**

```bash
git add src/carouauto/notifier.py tests/test_notifier.py
git commit -m "feat: add keyboard, edit_message, and answer_callback support to TelegramNotifier"
```

---

### Task 3: `SubscriptionStore.get_search_by_id`

**Files:**
- Modify: `src/carouauto/subscriptions.py`
- Test: `tests/test_subscriptions.py`

**Interfaces:**
- Produces: `SubscriptionStore.get_search_by_id(chat_id: int, search_id: int) -> UserSearch | None` — `None` both when the id doesn't exist and when it belongs to a different chat_id, so callers can't distinguish "doesn't exist" from "not yours" (deliberately — no information leak either way).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_subscriptions.py — add near test_add_search_then_get_and_list
def test_get_search_by_id_returns_the_search_when_owned(tmp_path):
    store = SubscriptionStore(str(tmp_path / "t.sqlite3"))
    store.register(222)
    search_id = store.add_search(222, "speediance", "https://example.com/s")

    found = store.get_search_by_id(222, search_id)

    assert found is not None
    assert found.search_id == search_id
    assert found.name == "speediance"


def test_get_search_by_id_returns_none_for_a_different_owner(tmp_path):
    store = SubscriptionStore(str(tmp_path / "t.sqlite3"))
    store.register(222)
    store.register(333)
    search_id = store.add_search(222, "speediance", "https://example.com/s")

    assert store.get_search_by_id(333, search_id) is None


def test_get_search_by_id_returns_none_for_an_unknown_id(tmp_path):
    store = SubscriptionStore(str(tmp_path / "t.sqlite3"))
    store.register(222)

    assert store.get_search_by_id(222, 999999) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_subscriptions.py -v -k get_search_by_id`
Expected: FAIL — `AttributeError: 'SubscriptionStore' object has no attribute 'get_search_by_id'`

- [ ] **Step 3: Write minimal implementation**

Add to `SubscriptionStore`, right after `get_search`:

```python
    def get_search_by_id(self, chat_id: int, search_id: int) -> UserSearch | None:
        row = self._conn.execute(
            f"SELECT {_SEARCH_COLUMNS} FROM user_searches WHERE chat_id = ? AND search_id = ?",
            (chat_id, search_id),
        ).fetchone()
        return _row_to_user_search(row) if row else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_subscriptions.py -v -k get_search_by_id`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/carouauto/subscriptions.py tests/test_subscriptions.py
git commit -m "feat: add SubscriptionStore.get_search_by_id, scoped to the owning chat_id"
```

---

### Task 4: `callbacks.py` — routing, ownership, and the first two verbs

**Files:**
- Create: `src/carouauto/callbacks.py`
- Modify: `src/carouauto/ui.py`
- Test: `tests/test_callbacks.py`
- Test: `tests/test_ui.py`

**Interfaces:**
- Consumes: `ui.inline_keyboard` (Task 1); `SubscriptionStore.is_active`, `.list_searches`, `.get_search_by_id`, `.set_paused`, `.set_hide_bumped` (existing + Task 3); `commands.BotContext` (existing).
- Produces: `ui.searches_list_keyboard(subs: list[UserSearch]) -> dict`, `ui.search_panel_text(sub: UserSearch) -> str`, `ui.search_panel_keyboard(sub: UserSearch) -> dict` (minimal: pause toggle, hide-bumped toggle, back — Task 10 extends this same function). `callbacks.CallbackResult` (dataclass: `text: str`, `reply_markup: dict | None`, `toast: str | None = None`, `force_reply_prompt: str | None = None`). `callbacks.dispatch_callback(data: str, chat_id: int, ctx: BotContext) -> CallbackResult` — never raises.

**Callback data verbs introduced in this task:** `ls` (show searches list), `sp:<id>` (open panel), `tg:<id>:pa` (toggle pause), `tg:<id>:hb` (toggle hide_bumped).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ui.py — add
from carouauto.subscriptions import UserSearch


def make_sub(search_id=1, chat_id=111, name="speediance", paused=False, hide_bumped=True,
             min_price=None, max_price=None, exclude_keywords=None, condition_filter=None,
             url="https://example.com"):
    return UserSearch(
        search_id=search_id, chat_id=chat_id, name=name, url=url,
        min_price=min_price, max_price=max_price, exclude_keywords=exclude_keywords,
        condition_filter=condition_filter, paused=paused, hide_bumped=hide_bumped,
    )


def test_searches_list_keyboard_has_one_button_per_search():
    subs = [make_sub(search_id=1, name="speediance"), make_sub(search_id=2, name="switch")]

    markup = ui.searches_list_keyboard(subs)

    buttons = markup["inline_keyboard"]
    assert [row[0]["text"] for row in buttons] == ["speediance", "switch"]
    assert [row[0]["callback_data"] for row in buttons] == ["sp:1", "sp:2"]


def test_search_panel_text_reflects_state():
    sub = make_sub(name="speediance", paused=True, hide_bumped=False)

    text = ui.search_panel_text(sub)

    assert "speediance" in text
    assert "paused" in text.lower()


def test_search_panel_keyboard_toggle_labels_reflect_current_state():
    sub = make_sub(search_id=7, paused=False, hide_bumped=True)

    markup = ui.search_panel_keyboard(sub)

    flat = [btn for row in markup["inline_keyboard"] for btn in row]
    by_data = {btn["callback_data"]: btn["text"] for btn in flat}
    assert by_data["tg:7:pa"] == "⏸ Pause"
    assert by_data["tg:7:hb"] == "👁 Show bumped"
    assert "ls" in by_data
```

```python
# tests/test_callbacks.py
import pytest

from carouauto.commands import BotContext
from carouauto.callbacks import CallbackResult, dispatch_callback
from carouauto.db import SeenStore
from carouauto.subscriptions import SubscriptionStore


def make_ctx(tmp_path):
    return BotContext(
        subscriptions=SubscriptionStore(str(tmp_path / "t.sqlite3")),
        seen_store=SeenStore(str(tmp_path / "t.sqlite3")),
        notifier=None,
        registration_password="pw",
        fetch_html=None,
        states={},
        db_path=str(tmp_path / "t.sqlite3"),
        poll_interval_seconds=300,
    )


@pytest.mark.asyncio
async def test_ls_shows_the_searches_list(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    ctx.subscriptions.add_search(111, "speediance", "https://example.com/s")

    result = await dispatch_callback("ls", 111, ctx)

    assert "speediance" in result.text
    assert result.reply_markup["inline_keyboard"][0][0]["callback_data"].startswith("sp:")


@pytest.mark.asyncio
async def test_ls_for_an_inactive_user_asks_them_to_register(tmp_path):
    ctx = make_ctx(tmp_path)

    result = await dispatch_callback("ls", 111, ctx)

    assert "register" in result.text.lower()
    assert result.reply_markup is None


@pytest.mark.asyncio
async def test_sp_opens_the_panel_for_an_owned_search(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    search_id = ctx.subscriptions.add_search(111, "speediance", "https://example.com/s")

    result = await dispatch_callback(f"sp:{search_id}", 111, ctx)

    assert "speediance" in result.text
    flat = [b for row in result.reply_markup["inline_keyboard"] for b in row]
    assert any(b["callback_data"] == f"tg:{search_id}:pa" for b in flat)


@pytest.mark.asyncio
async def test_sp_rejects_a_search_owned_by_someone_else(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    ctx.subscriptions.register(222)
    search_id = ctx.subscriptions.add_search(222, "someone-elses", "https://example.com/s")

    result = await dispatch_callback(f"sp:{search_id}", 111, ctx)

    assert "no longer exists" in result.text.lower()
    assert result.reply_markup is None
    assert result.toast is not None


@pytest.mark.asyncio
async def test_sp_rejects_an_unknown_search_id(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)

    result = await dispatch_callback("sp:999999", 111, ctx)

    assert "no longer exists" in result.text.lower()


@pytest.mark.asyncio
async def test_tg_pa_toggles_pause_and_re_renders_the_panel(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    search_id = ctx.subscriptions.add_search(111, "speediance", "https://example.com/s")

    result = await dispatch_callback(f"tg:{search_id}:pa", 111, ctx)

    assert ctx.subscriptions.get_search_by_id(111, search_id).paused is True
    assert "resume" in result.text.lower() or "▶" in result.text
    flat = [b for row in result.reply_markup["inline_keyboard"] for b in row]
    assert any(b["callback_data"] == f"tg:{search_id}:pa" for b in flat)


@pytest.mark.asyncio
async def test_tg_hb_toggles_hide_bumped(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    search_id = ctx.subscriptions.add_search(111, "speediance", "https://example.com/s")
    assert ctx.subscriptions.get_search_by_id(111, search_id).hide_bumped is True  # default

    result = await dispatch_callback(f"tg:{search_id}:hb", 111, ctx)

    assert ctx.subscriptions.get_search_by_id(111, search_id).hide_bumped is False


@pytest.mark.asyncio
async def test_unknown_verb_returns_a_generic_error_without_raising(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)

    result = await dispatch_callback("nonsense:1:2:3", 111, ctx)

    assert isinstance(result, CallbackResult)
    assert result.reply_markup is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_ui.py tests/test_callbacks.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'carouauto.callbacks'`, `AttributeError: module 'carouauto.ui' has no attribute 'searches_list_keyboard'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/carouauto/ui.py — append
from .subscriptions import UserSearch


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
    lines.append("Status: paused" if sub.paused else "Status: active")
    return "\n".join(lines)


def search_panel_keyboard(sub: UserSearch) -> dict:
    pause_label = "▶️ Resume" if sub.paused else "⏸ Pause"
    bumped_label = "👁 Show bumped" if sub.hide_bumped else "🙈 Hide bumped"
    return inline_keyboard(
        [
            [(pause_label, f"tg:{sub.search_id}:pa"), (bumped_label, f"tg:{sub.search_id}:hb")],
            [("⬅️ Back", "ls")],
        ]
    )
```

```python
# src/carouauto/callbacks.py
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
    return CallbackResult(text="Your tracked searches:", reply_markup=ui.searches_list_keyboard(subs))


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
            sub = ctx.subscriptions.get_search_by_id(chat_id, search_id)
            if sub is None:
                return _stale_search_result()
            if field == "pa":
                ctx.subscriptions.set_paused(chat_id, sub.name, not sub.paused)
            elif field == "hb":
                ctx.subscriptions.set_hide_bumped(chat_id, sub.name, not sub.hide_bumped)
            return _panel_result(chat_id, search_id, ctx)

        return CallbackResult(text="Unknown action.", reply_markup=None, toast="Unknown action")
    except Exception as exc:
        logger.error("error handling callback '%s': %s", data, type(exc).__name__)
        return CallbackResult(text="Something went wrong.", reply_markup=None, toast="Error")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_ui.py tests/test_callbacks.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add src/carouauto/ui.py src/carouauto/callbacks.py tests/test_ui.py tests/test_callbacks.py
git commit -m "feat: add callbacks.py with ownership-checked ls/sp/tg routing"
```

- [ ] **Step 6: Push**

```bash
gh auth switch --hostname github.com --user ststheting
git push origin main
gh auth switch --hostname github.com --user hmishaun
```

---

### Task 5: Wire callbacks into the listener; `/searches` sends the button list

**Files:**
- Modify: `src/carouauto/telegram_listener.py`
- Modify: `src/carouauto/commands.py`
- Test: `tests/test_telegram_listener.py` (new)
- Test: `tests/test_commands.py`

**Interfaces:**
- Consumes: `callbacks.dispatch_callback` (Task 4), `ui.searches_list_keyboard` (Task 4), `notifier.edit_message`/`answer_callback` (Task 2).
- Produces: `telegram_listener.handle_update(update: dict, ctx: BotContext) -> None` — extracted from the loop body so it's directly testable; routes `message` updates through the existing `dispatch()` and `callback_query` updates through `dispatch_callback()`.

**Behavior change:** `handle_searches` no longer returns the list as plain text — it sends a button-keyboard message via `ctx.notifier` and returns `""`. Update the two existing tests that call it directly.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_commands.py — replace the body of the two existing handle_searches tests
@pytest.mark.asyncio
async def test_handle_searches_sends_a_button_per_search(tmp_path):
    ctx = make_ctx(tmp_path)
    await handle_addurl(["speediance", "https://example.com/s", "100", "500"], 111, ctx)
    sent = []

    class FakeNotifier:
        def send_text(self, chat_id, text, reply_markup=None):
            sent.append((chat_id, text, reply_markup))

    ctx.notifier = FakeNotifier()

    reply = await handle_searches([], 111, ctx)

    assert reply == ""
    assert len(sent) == 1
    chat_id, text, reply_markup = sent[0]
    assert chat_id == 111
    assert reply_markup["inline_keyboard"][0][0]["text"] == "speediance"


@pytest.mark.asyncio
async def test_handle_searches_with_none_tracked_sends_a_plain_message(tmp_path):
    ctx = make_ctx(tmp_path)
    sent = []

    class FakeNotifier:
        def send_text(self, chat_id, text, reply_markup=None):
            sent.append((chat_id, text, reply_markup))

    ctx.notifier = FakeNotifier()

    reply = await handle_searches([], 111, ctx)

    assert reply == ""
    assert "no tracked searches" in sent[0][1].lower()
    assert sent[0][2] is None
```

Remove (or replace in place — same names, new bodies) the old `test_handle_searches_lists_names_and_filters` and `test_handle_searches_with_none_tracked` tests that asserted on the returned string.

```python
# tests/test_telegram_listener.py
import pytest

from carouauto.commands import BotContext
from carouauto.db import SeenStore
from carouauto.subscriptions import SubscriptionStore
from carouauto.telegram_listener import handle_update


class FakeNotifier:
    def __init__(self):
        self.sent = []
        self.edits = []
        self.answers = []

    def send_text(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text, reply_markup))

    def edit_message(self, chat_id, message_id, text, reply_markup):
        self.edits.append((chat_id, message_id, text, reply_markup))

    def answer_callback(self, callback_query_id, text=None):
        self.answers.append((callback_query_id, text))


def make_ctx(tmp_path):
    return BotContext(
        subscriptions=SubscriptionStore(str(tmp_path / "t.sqlite3")),
        seen_store=SeenStore(str(tmp_path / "t.sqlite3")),
        notifier=FakeNotifier(),
        registration_password="pw",
        fetch_html=None,
        states={},
        db_path=str(tmp_path / "t.sqlite3"),
        poll_interval_seconds=300,
    )


@pytest.mark.asyncio
async def test_handle_update_routes_a_plain_command(tmp_path):
    ctx = make_ctx(tmp_path)
    update = {"update_id": 1, "message": {"chat": {"id": 111}, "text": "/start"}}

    await handle_update(update, ctx)

    assert len(ctx.notifier.sent) == 1
    assert "register" in ctx.notifier.sent[0][1].lower()


@pytest.mark.asyncio
async def test_handle_update_routes_a_callback_query_and_always_answers(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    update = {
        "update_id": 2,
        "callback_query": {
            "id": "cbq1",
            "data": "ls",
            "message": {"chat": {"id": 111}, "message_id": 42},
        },
    }

    await handle_update(update, ctx)

    assert ctx.notifier.edits == [(111, 42, "You have no tracked searches yet. Use /add <name> to start one.", None)]
    assert ctx.notifier.answers == [("cbq1", None)]


@pytest.mark.asyncio
async def test_handle_update_ignores_a_message_with_no_text(tmp_path):
    ctx = make_ctx(tmp_path)
    update = {"update_id": 3, "message": {"chat": {"id": 111}}}

    await handle_update(update, ctx)  # must not raise

    assert ctx.notifier.sent == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_commands.py tests/test_telegram_listener.py -v`
Expected: FAIL — `handle_searches` still returns a string; `handle_update` doesn't exist yet.

- [ ] **Step 3: Write minimal implementation**

```python
# src/carouauto/commands.py — replace handle_searches
async def handle_searches(args: list[str], chat_id: int, ctx: BotContext) -> str:
    subs = ctx.subscriptions.list_searches(chat_id)
    if not subs:
        ctx.notifier.send_text(chat_id, "You have no tracked searches yet. Use /add <name> to start one.")
        return ""
    ctx.notifier.send_text(chat_id, "Your tracked searches:", reply_markup=ui.searches_list_keyboard(subs))
    return ""
```

Add the import at the top of `commands.py`:

```python
from . import ui
```

```python
# src/carouauto/telegram_listener.py — replace the file's per-update loop body
from __future__ import annotations

import asyncio
import logging

import httpx

from .callbacks import dispatch_callback
from .commands import BotContext, dispatch, parse_command

logger = logging.getLogger("carouauto")

TELEGRAM_API_BASE = "https://api.telegram.org"
LONG_POLL_TIMEOUT_SECONDS = 30

BOT_COMMANDS = [
    # ... unchanged, keep the existing list ...
]


async def set_bot_commands(client: httpx.AsyncClient, bot_token: str) -> None:
    # ... unchanged ...


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
        parsed = parse_command(message["text"])
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
        result = await dispatch_callback(data, chat_id, ctx)
        try:
            ctx.notifier.edit_message(chat_id, message_id, result.text, result.reply_markup)
        except Exception as exc:
            logger.error("failed to edit message for callback '%s': %s", data, type(exc).__name__)
        if result.force_reply_prompt:
            ctx.notifier.send_text(chat_id, result.force_reply_prompt, reply_markup={"force_reply": True})
        try:
            ctx.notifier.answer_callback(callback_query_id, result.toast)
        except Exception as exc:
            logger.error("failed to answer callback '%s': %s", data, type(exc).__name__)


async def run_command_listener(bot_token: str, ctx: BotContext) -> None:
    offset: int | None = None
    async with httpx.AsyncClient() as client:
        try:
            await set_bot_commands(client, bot_token)
        except httpx.HTTPError as exc:
            logger.error("failed to set bot command menu: %s", type(exc).__name__)

        while True:
            try:
                updates = await get_updates(client, bot_token, offset)
            except httpx.HTTPError as exc:
                logger.error("error polling Telegram updates: %s", type(exc).__name__)
                await asyncio.sleep(5)
                continue
            except Exception as exc:
                logger.error("unexpected error in command listener: %s", type(exc).__name__)
                await asyncio.sleep(5)
                continue

            for update in updates:
                offset = update["update_id"] + 1
                await handle_update(update, ctx)
```

Keep the existing `BOT_COMMANDS` list and `set_bot_commands` function bodies exactly as they are today — only `get_updates`, and the loop-body-to-`handle_update` extraction, change in this task.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_commands.py tests/test_telegram_listener.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS — this is the point where a regression in an unrelated test file (e.g. a leftover call to `handle_searches` elsewhere) would show up.

- [ ] **Step 6: Commit and push**

```bash
git add src/carouauto/commands.py src/carouauto/telegram_listener.py tests/test_commands.py tests/test_telegram_listener.py
git commit -m "feat: route callback_query updates; /searches sends a button list"
gh auth switch --hostname github.com --user ststheting
git push origin main
gh auth switch --hostname github.com --user hmishaun
```

**Layer 0 is now deployable.** `/searches` is button-driven end to end: tap a search, toggle pause/hide-bumped, tap Back. Typed commands are untouched. This is a natural point to deploy to the VPS and confirm live before continuing.

---

## Layer 1: Search panels

### Task 6: Condition picker

**Files:**
- Modify: `src/carouauto/ui.py`
- Modify: `src/carouauto/callbacks.py`
- Test: `tests/test_ui.py`
- Test: `tests/test_callbacks.py`

**Interfaces:**
- Produces: `ui.CONDITIONS: tuple[str, ...] = ("Brand new", "Like new", "Lightly used", "Well used", "Heavily used")`, `ui.condition_keyboard(sub: UserSearch) -> dict`. New verbs `cd:<id>` (open picker) and `cds:<id>:<idx>` (`idx` 0-4 → `CONDITIONS[idx]`, `idx` 5 → clear/"Any").

**Verb reference so far:** `ls`, `sp:<id>`, `tg:<id>:pa`, `tg:<id>:hb`, `cd:<id>`, `cds:<id>:<idx>`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ui.py — add
def test_condition_keyboard_has_one_button_per_condition_plus_any():
    sub = make_sub(search_id=3)

    markup = ui.condition_keyboard(sub)

    flat = [b for row in markup["inline_keyboard"] for b in row]
    assert [b["text"] for b in flat[:-2]] == list(ui.CONDITIONS)
    assert flat[-2]["text"] == "Any"
    assert flat[-2]["callback_data"] == "cds:3:5"
    assert flat[-1]["callback_data"] == "sp:3"  # back to panel
```

```python
# tests/test_callbacks.py — add
@pytest.mark.asyncio
async def test_cd_opens_the_condition_picker(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    search_id = ctx.subscriptions.add_search(111, "speediance", "https://example.com/s")

    result = await dispatch_callback(f"cd:{search_id}", 111, ctx)

    flat = [b for row in result.reply_markup["inline_keyboard"] for b in row]
    assert any(b["text"] == "Brand new" for b in flat)


@pytest.mark.asyncio
async def test_cds_sets_the_condition_and_returns_to_the_panel(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    search_id = ctx.subscriptions.add_search(111, "speediance", "https://example.com/s")

    result = await dispatch_callback(f"cds:{search_id}:0", 111, ctx)

    assert ctx.subscriptions.get_search_by_id(111, search_id).condition_filter == "Brand new"
    assert "speediance" in result.text  # back on the panel


@pytest.mark.asyncio
async def test_cds_any_clears_the_condition(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    search_id = ctx.subscriptions.add_search(111, "speediance", "https://example.com/s")
    ctx.subscriptions.set_condition_filter(111, "speediance", "Brand new")

    await dispatch_callback(f"cds:{search_id}:5", 111, ctx)

    assert ctx.subscriptions.get_search_by_id(111, search_id).condition_filter is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_ui.py tests/test_callbacks.py -v -k "condition or cd_ or cds"`
Expected: FAIL — `AttributeError: module 'carouauto.ui' has no attribute 'condition_keyboard'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/carouauto/ui.py — append
CONDITIONS = ("Brand new", "Like new", "Lightly used", "Well used", "Heavily used")


def condition_keyboard(sub: UserSearch) -> dict:
    rows = [[(cond, f"cds:{sub.search_id}:{i}")] for i, cond in enumerate(CONDITIONS)]
    rows.append([("Any", f"cds:{sub.search_id}:5")])
    rows.append([("⬅️ Back", f"sp:{sub.search_id}")])
    return inline_keyboard(rows)
```

```python
# src/carouauto/callbacks.py — add two branches inside dispatch_callback's if-chain,
# after the "tg" branch and before the final "unknown verb" return:

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_ui.py tests/test_callbacks.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add src/carouauto/ui.py src/carouauto/callbacks.py tests/test_ui.py tests/test_callbacks.py
git commit -m "feat: add condition picker to the search panel"
```

---

### Task 7: `PendingInput` and ForceReply prompts for price/exclude

**Files:**
- Modify: `src/carouauto/commands.py`
- Modify: `src/carouauto/callbacks.py`
- Test: `tests/test_callbacks.py`
- Test: `tests/test_commands.py`

**Interfaces:**
- Produces: `commands.PendingInput` (dataclass: `kind: str`, `search_id: int | None`, `created_at: datetime`). `BotContext.pending: dict[int, PendingInput]` (new field, default `{}`). `commands.PENDING_EXPIRY_SECONDS = 600`. New verbs `pp:<id>` (prompt set price) and `px:<id>` (prompt set exclude words) — both return a `CallbackResult` with `force_reply_prompt` set and a side effect of writing `ctx.pending[chat_id]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_commands.py — add near the BotContext-adjacent tests
def test_bot_context_pending_defaults_to_empty_dict(tmp_path):
    ctx = make_ctx(tmp_path)

    assert ctx.pending == {}
```

```python
# tests/test_callbacks.py — add
from datetime import datetime, timezone


@pytest.mark.asyncio
async def test_pp_prompts_for_price_and_records_pending_state(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    search_id = ctx.subscriptions.add_search(111, "speediance", "https://example.com/s")

    result = await dispatch_callback(f"pp:{search_id}", 111, ctx)

    assert result.force_reply_prompt is not None
    assert "min" in result.force_reply_prompt.lower()
    assert ctx.pending[111].kind == "setprice"
    assert ctx.pending[111].search_id == search_id


@pytest.mark.asyncio
async def test_px_prompts_for_exclude_words_and_records_pending_state(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    search_id = ctx.subscriptions.add_search(111, "speediance", "https://example.com/s")

    result = await dispatch_callback(f"px:{search_id}", 111, ctx)

    assert result.force_reply_prompt is not None
    assert ctx.pending[111].kind == "setexclude"
    assert ctx.pending[111].search_id == search_id


@pytest.mark.asyncio
async def test_pp_on_a_stale_search_does_not_set_pending_state(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)

    result = await dispatch_callback("pp:999999", 111, ctx)

    assert "no longer exists" in result.text.lower()
    assert 111 not in ctx.pending
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_commands.py tests/test_callbacks.py -v -k "pending or pp_ or px_"`
Expected: FAIL — `TypeError: BotContext.__init__() got an unexpected keyword argument` is NOT expected since `pending` has a default; instead expect `AttributeError: 'BotContext' object has no attribute 'pending'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/carouauto/commands.py — near the top, alongside other imports
from dataclasses import dataclass, field
from datetime import datetime, timezone
```

(`dataclass` is already imported without `field` — extend the existing import line rather than duplicating it. `datetime`/`timezone` are new to this file.)

```python
# src/carouauto/commands.py — add after the BotContext class
PENDING_EXPIRY_SECONDS = 600


@dataclass
class PendingInput:
    kind: str  # "setprice" | "setexclude" | "add_query" | "add_price"
    search_id: int | None
    created_at: datetime
```

Add `pending: dict[int, PendingInput] = field(default_factory=dict)` as a new field on `BotContext`, after `poll_interval_seconds`.

```python
# src/carouauto/callbacks.py — new imports
from datetime import datetime, timezone

from .commands import BotContext, PendingInput
```

Add two branches inside `dispatch_callback`'s if-chain, after the `cds` branch:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_commands.py tests/test_callbacks.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add src/carouauto/commands.py src/carouauto/callbacks.py tests/test_commands.py tests/test_callbacks.py
git commit -m "feat: add PendingInput and ForceReply prompts for price/exclude"
```

---

### Task 8: Listener applies pending replies

**Files:**
- Modify: `src/carouauto/telegram_listener.py`
- Test: `tests/test_telegram_listener.py`

**Interfaces:**
- Consumes: `ctx.pending` (Task 7), `SubscriptionStore.set_price_filter`, `.set_exclude_keywords` (existing).
- Produces: `handle_update` now checks `ctx.pending` for non-command text messages before falling through to the existing "ignore" behavior.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_telegram_listener.py — add
from datetime import datetime, timedelta, timezone

from carouauto.commands import PendingInput


@pytest.mark.asyncio
async def test_a_reply_while_pending_setprice_updates_the_search(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    search_id = ctx.subscriptions.add_search(111, "speediance", "https://example.com/s")
    ctx.pending[111] = PendingInput(kind="setprice", search_id=search_id, created_at=datetime.now(timezone.utc))
    update = {"update_id": 1, "message": {"chat": {"id": 111}, "text": "100 500"}}

    await handle_update(update, ctx)

    found = ctx.subscriptions.get_search_by_id(111, search_id)
    assert (found.min_price, found.max_price) == (100.0, 500.0)
    assert 111 not in ctx.pending  # consumed
    assert ctx.notifier.sent  # a confirmation, re-rendered panel, was sent


@pytest.mark.asyncio
async def test_a_reply_of_none_clears_setprice(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    search_id = ctx.subscriptions.add_search(111, "speediance", "https://example.com/s")
    ctx.subscriptions.set_price_filter(111, "speediance", 10.0, 20.0)
    ctx.pending[111] = PendingInput(kind="setprice", search_id=search_id, created_at=datetime.now(timezone.utc))
    update = {"update_id": 1, "message": {"chat": {"id": 111}, "text": "none"}}

    await handle_update(update, ctx)

    found = ctx.subscriptions.get_search_by_id(111, search_id)
    assert (found.min_price, found.max_price) == (None, None)


@pytest.mark.asyncio
async def test_an_unparseable_price_reply_keeps_the_pending_state(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    search_id = ctx.subscriptions.add_search(111, "speediance", "https://example.com/s")
    ctx.pending[111] = PendingInput(kind="setprice", search_id=search_id, created_at=datetime.now(timezone.utc))
    update = {"update_id": 1, "message": {"chat": {"id": 111}, "text": "not a number"}}

    await handle_update(update, ctx)

    assert 111 in ctx.pending  # still waiting for a valid reply
    assert "number" in ctx.notifier.sent[-1][1].lower()


@pytest.mark.asyncio
async def test_a_reply_while_pending_setexclude_updates_the_search(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    search_id = ctx.subscriptions.add_search(111, "speediance", "https://example.com/s")
    ctx.pending[111] = PendingInput(kind="setexclude", search_id=search_id, created_at=datetime.now(timezone.utc))
    update = {"update_id": 1, "message": {"chat": {"id": 111}, "text": "case, box"}}

    await handle_update(update, ctx)

    found = ctx.subscriptions.get_search_by_id(111, search_id)
    assert found.exclude_keywords == "case, box"


@pytest.mark.asyncio
async def test_an_expired_pending_entry_is_ignored(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    search_id = ctx.subscriptions.add_search(111, "speediance", "https://example.com/s")
    old = datetime.now(timezone.utc) - timedelta(seconds=700)
    ctx.pending[111] = PendingInput(kind="setprice", search_id=search_id, created_at=old)
    update = {"update_id": 1, "message": {"chat": {"id": 111}, "text": "100 500"}}

    await handle_update(update, ctx)

    found = ctx.subscriptions.get_search_by_id(111, search_id)
    assert found.min_price is None  # not applied — the prompt had expired
    assert 111 not in ctx.pending  # expired entry is cleaned up


@pytest.mark.asyncio
async def test_a_command_while_pending_is_still_treated_as_a_command(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    search_id = ctx.subscriptions.add_search(111, "speediance", "https://example.com/s")
    ctx.pending[111] = PendingInput(kind="setprice", search_id=search_id, created_at=datetime.now(timezone.utc))
    update = {"update_id": 1, "message": {"chat": {"id": 111}, "text": "/status"}}

    await handle_update(update, ctx)

    assert 111 in ctx.pending  # untouched — /status isn't the awaited reply
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_telegram_listener.py -v -k pending`
Expected: FAIL — plain-text replies are currently silently ignored by `handle_update` (no pending handling exists yet).

- [ ] **Step 3: Write minimal implementation**

```python
# src/carouauto/telegram_listener.py — new imports
from datetime import datetime, timedelta, timezone

from . import ui
from .commands import PENDING_EXPIRY_SECONDS
from .filters import parse_price  # reuse the existing "S$599" -> 599.0 parser? No — plain "100" here.
```

`parse_price` expects Carousell-style price text (`"S$599"`); the ForceReply value here is a bare number typed by the user, so don't reuse it — write a small local parser instead. Remove the `from .filters import parse_price` line above; it isn't needed.

Replace `handle_update`'s `message` branch:

```python
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
        result = await dispatch_callback(data, chat_id, ctx)
        try:
            ctx.notifier.edit_message(chat_id, message_id, result.text, result.reply_markup)
        except Exception as exc:
            logger.error("failed to edit message for callback '%s': %s", data, type(exc).__name__)
        if result.force_reply_prompt:
            ctx.notifier.send_text(chat_id, result.force_reply_prompt, reply_markup={"force_reply": True})
        try:
            ctx.notifier.answer_callback(callback_query_id, result.toast)
        except Exception as exc:
            logger.error("failed to answer callback '%s': %s", data, type(exc).__name__)


def _parse_two_optional_numbers(text: str) -> tuple[float | None, float | None] | None:
    if text.strip().lower() == "none":
        return None, None
    parts = text.split()
    if len(parts) != 2:
        return "invalid"
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        return "invalid"


async def _apply_pending_reply(pending, text: str, chat_id: int, ctx: BotContext) -> None:
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_telegram_listener.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Run the full suite, commit, push**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS

```bash
git add src/carouauto/telegram_listener.py tests/test_telegram_listener.py
git commit -m "feat: apply ForceReply replies for pending price/exclude prompts"
gh auth switch --hostname github.com --user ststheting
git push origin main
gh auth switch --hostname github.com --user hmishaun
```

---

### Task 9: Remove with confirmation

**Files:**
- Modify: `src/carouauto/ui.py`
- Modify: `src/carouauto/callbacks.py`
- Test: `tests/test_ui.py`
- Test: `tests/test_callbacks.py`

**Interfaces:**
- Produces: `ui.remove_confirm_keyboard(search_id: int) -> dict`. New verbs `rm:<id>` (ask to confirm), `rmy:<id>` (confirmed — deletes), `rmn:<id>` (cancel, back to panel).

**Full verb reference after this task:** `ls`, `sp:<id>`, `tg:<id>:pa`, `tg:<id>:hb`, `cd:<id>`, `cds:<id>:<idx>`, `pp:<id>`, `px:<id>`, `rm:<id>`, `rmy:<id>`, `rmn:<id>`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ui.py — add
def test_remove_confirm_keyboard_has_yes_and_no():
    markup = ui.remove_confirm_keyboard(9)

    flat = [b for row in markup["inline_keyboard"] for b in row]
    by_data = {b["callback_data"]: b["text"] for b in flat}
    assert by_data["rmy:9"] == "Yes, remove it"
    assert by_data["rmn:9"] == "Cancel"
```

```python
# tests/test_callbacks.py — add
@pytest.mark.asyncio
async def test_rm_asks_for_confirmation_without_deleting(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    search_id = ctx.subscriptions.add_search(111, "speediance", "https://example.com/s")

    result = await dispatch_callback(f"rm:{search_id}", 111, ctx)

    assert "sure" in result.text.lower()
    assert ctx.subscriptions.get_search_by_id(111, search_id) is not None


@pytest.mark.asyncio
async def test_rmy_deletes_the_search_and_its_seen_history(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    search_id = ctx.subscriptions.add_search(111, "speediance", "https://example.com/s")
    ctx.seen_store.mark_seen(search_id, ["1", "2"])

    result = await dispatch_callback(f"rmy:{search_id}", 111, ctx)

    assert ctx.subscriptions.get_search_by_id(111, search_id) is None
    assert ctx.seen_store.get_new_ids(search_id, ["1", "2"]) == []  # first-run semantics restored
    assert "removed" in result.text.lower()


@pytest.mark.asyncio
async def test_rmn_cancels_back_to_the_panel(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    search_id = ctx.subscriptions.add_search(111, "speediance", "https://example.com/s")

    result = await dispatch_callback(f"rmn:{search_id}", 111, ctx)

    assert ctx.subscriptions.get_search_by_id(111, search_id) is not None
    assert "speediance" in result.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_ui.py tests/test_callbacks.py -v -k "remove or rm_ or rmy or rmn"`
Expected: FAIL — `AttributeError: module 'carouauto.ui' has no attribute 'remove_confirm_keyboard'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/carouauto/ui.py — append
def remove_confirm_keyboard(search_id: int) -> dict:
    return inline_keyboard(
        [
            [("Yes, remove it", f"rmy:{search_id}"), ("Cancel", f"rmn:{search_id}")],
        ]
    )
```

```python
# src/carouauto/callbacks.py — add three branches, after the "px" branch:

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_ui.py tests/test_callbacks.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add src/carouauto/ui.py src/carouauto/callbacks.py tests/test_ui.py tests/test_callbacks.py
git commit -m "feat: add remove-with-confirmation to the search panel"
```

---

### Task 10: Wire the full panel keyboard together

**Files:**
- Modify: `src/carouauto/ui.py`
- Test: `tests/test_ui.py`

**Interfaces:**
- Produces: `ui.search_panel_keyboard` now includes the condition, set-price, exclude-words, and remove rows added across Tasks 6, 7, and 9 (those tasks wired their own callbacks but `search_panel_keyboard` itself — the button set a user actually sees — hasn't grown since Task 4's minimal 2-toggle version). No new verbs.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ui.py — replace test_search_panel_keyboard_toggle_labels_reflect_current_state
def test_search_panel_keyboard_includes_every_action():
    sub = make_sub(search_id=7, paused=False, hide_bumped=True)

    markup = ui.search_panel_keyboard(sub)

    flat = [b["callback_data"] for row in markup["inline_keyboard"] for b in row]
    assert "tg:7:pa" in flat
    assert "tg:7:hb" in flat
    assert "cd:7" in flat
    assert "pp:7" in flat
    assert "px:7" in flat
    assert "rm:7" in flat
    assert "ls" in flat
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_ui.py -v -k includes_every_action`
Expected: FAIL — the current keyboard only has `tg:7:pa`, `tg:7:hb`, and `ls`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/carouauto/ui.py — replace search_panel_keyboard
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_ui.py tests/test_callbacks.py -v`
Expected: PASS — including every earlier `search_panel_keyboard`-touching test in `test_callbacks.py`, since those only assert `any(b["callback_data"] == ...)`, not the full row layout.

- [ ] **Step 5: Run the full suite, commit, push**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS

```bash
git add src/carouauto/ui.py tests/test_ui.py
git commit -m "feat: complete the search panel keyboard with all actions"
gh auth switch --hostname github.com --user ststheting
git push origin main
gh auth switch --hostname github.com --user hmishaun
```

**Layer 1 is now deployable.** Every filter (price, exclude words, condition, pause, hide-bumped) and removal is reachable entirely by tapping through `/searches`. This is a natural point to deploy and confirm live before continuing.

---

## Layer 2: Guided add

### Task 11: `/add` with no args starts a guided flow

**Files:**
- Modify: `src/carouauto/commands.py`
- Modify: `src/carouauto/ui.py`
- Test: `tests/test_commands.py`

**Interfaces:**
- Produces: `ui.add_flow_keyboard() -> dict` (buttons `afy` / `afp` / `afc` — no search id, since the search doesn't exist yet). `handle_add([], chat_id, ctx)` now sends a ForceReply prompt and sets `ctx.pending[chat_id] = PendingInput(kind="add_query", search_id=None, ...)` instead of returning a usage string.

**Behavior change:** the existing `test_handle_add_with_no_args_gives_usage` test's premise changes — update it.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_commands.py — replace test_handle_add_with_no_args_gives_usage
@pytest.mark.asyncio
async def test_handle_add_with_no_args_prompts_with_forcereply(tmp_path):
    ctx = make_ctx(tmp_path)
    sent = []

    class FakeNotifier:
        def send_text(self, chat_id, text, reply_markup=None):
            sent.append((chat_id, text, reply_markup))

    ctx.notifier = FakeNotifier()

    reply = await handle_add([], 111, ctx)

    assert reply == ""
    assert len(sent) == 1
    chat_id, text, reply_markup = sent[0]
    assert "search for" in text.lower()
    assert reply_markup == {"force_reply": True}
    assert ctx.pending[111].kind == "add_query"
    assert ctx.pending[111].search_id is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_commands.py -v -k add_with_no_args`
Expected: FAIL — `handle_add([], ...)` currently returns a usage string and never touches `ctx.notifier` or `ctx.pending`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/carouauto/commands.py — replace the len(args) < 1 branch inside handle_add
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
    return _finish_add(chat_id, name, url, min_price, max_price, ctx)
```

```python
# src/carouauto/ui.py — append
def add_flow_keyboard() -> dict:
    return inline_keyboard([[("✅ Add now", "afy"), ("💰 Set price range first", "afp")], [("Cancel", "afc")]])
```

`add_flow_keyboard` isn't consumed until Task 12 — this task only needs it to exist so its own test can import `ui` without error; no test in this task exercises it directly, but include it now so Task 12 doesn't need a separate `ui.py` edit.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_commands.py -v -k add_with_no_args`
Expected: PASS

- [ ] **Step 5: Run the full suite, commit**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS

```bash
git add src/carouauto/commands.py src/carouauto/ui.py tests/test_commands.py
git commit -m "feat: /add with no args starts a guided ForceReply flow"
```

---

### Task 12: Completing the guided add flow

**Files:**
- Modify: `src/carouauto/telegram_listener.py`
- Modify: `src/carouauto/callbacks.py`
- Test: `tests/test_telegram_listener.py`
- Test: `tests/test_callbacks.py`

**Interfaces:**
- Consumes: `PendingInput(kind="add_query")` (Task 11), `commands._build_carousell_search_url`, `commands._is_url`, `commands._finish_add` (existing, made non-underscore-importable within the package — they're already module-level functions, just imported directly by name).
- Produces: a reply to the `add_query` prompt stores the query text in `ctx.pending[chat_id]` (reusing the same entry, now carrying the query) and sends the `afy`/`afp`/`afc` buttons. `afy` finishes the add immediately; `afp` sends a second ForceReply (`kind="add_price"`) for the price range; `afc` cancels and clears pending state.

**New verbs:** `afy`, `afp`, `afc` (none carry a search id — the search doesn't exist until `afy`/the price reply commits it).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_callbacks.py — add
@pytest.mark.asyncio
async def test_afy_creates_the_search_from_the_pending_query(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    ctx.pending[111] = PendingInput(kind="add_query", search_id=None, created_at=datetime.now(timezone.utc))
    ctx.pending[111].query = "speediance"  # set by the listener before this callback fires, per Task 12 Step 3

    result = await dispatch_callback("afy", 111, ctx)

    found = ctx.subscriptions.get_search(111, "speediance")
    assert found is not None
    assert found.url == "https://www.carousell.sg/search/speediance?sort_by=3"
    assert "added" in result.text.lower()
    assert 111 not in ctx.pending


@pytest.mark.asyncio
async def test_afp_prompts_for_a_price_range(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    ctx.pending[111] = PendingInput(kind="add_query", search_id=None, created_at=datetime.now(timezone.utc))
    ctx.pending[111].query = "speediance"

    result = await dispatch_callback("afp", 111, ctx)

    assert result.force_reply_prompt is not None
    assert ctx.pending[111].kind == "add_price"
    assert ctx.pending[111].query == "speediance"  # carried forward


@pytest.mark.asyncio
async def test_afc_cancels_the_add_flow(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    ctx.pending[111] = PendingInput(kind="add_query", search_id=None, created_at=datetime.now(timezone.utc))
    ctx.pending[111].query = "speediance"

    result = await dispatch_callback("afc", 111, ctx)

    assert "cancelled" in result.text.lower()
    assert 111 not in ctx.pending
    assert ctx.subscriptions.get_search(111, "speediance") is None
```

```python
# tests/test_telegram_listener.py — add
@pytest.mark.asyncio
async def test_a_reply_while_pending_add_query_sends_the_add_flow_buttons(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    ctx.pending[111] = PendingInput(kind="add_query", search_id=None, created_at=datetime.now(timezone.utc))
    update = {"update_id": 1, "message": {"chat": {"id": 111}, "text": "speediance"}}

    await handle_update(update, ctx)

    assert ctx.pending[111].kind == "add_query"
    assert ctx.pending[111].query == "speediance"
    chat_id, text, reply_markup = ctx.notifier.sent[-1]
    flat = [b["callback_data"] for row in reply_markup["inline_keyboard"] for b in row]
    assert flat == ["afy", "afp", "afc"]


@pytest.mark.asyncio
async def test_a_reply_while_pending_add_price_finishes_the_add(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    ctx.pending[111] = PendingInput(kind="add_price", search_id=None, created_at=datetime.now(timezone.utc))
    ctx.pending[111].query = "speediance"
    update = {"update_id": 1, "message": {"chat": {"id": 111}, "text": "100 500"}}

    await handle_update(update, ctx)

    found = ctx.subscriptions.get_search(111, "speediance")
    assert (found.min_price, found.max_price) == (100.0, 500.0)
    assert 111 not in ctx.pending
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_callbacks.py tests/test_telegram_listener.py -v -k "afy or afp or afc or add_query or add_price"`
Expected: FAIL — `PendingInput` has no `query` attribute yet; `afy`/`afp`/`afc` verbs don't exist; the listener doesn't special-case `add_query`/`add_price` pending kinds.

- [ ] **Step 3: Write minimal implementation**

```python
# src/carouauto/commands.py — add a query field to PendingInput
@dataclass
class PendingInput:
    kind: str  # "setprice" | "setexclude" | "add_query" | "add_price"
    search_id: int | None
    created_at: datetime
    query: str | None = None
```

```python
# src/carouauto/callbacks.py — new imports
from .commands import BotContext, PendingInput, _build_carousell_search_url, _finish_add, _is_url
```

Add three branches to `dispatch_callback`, after the `rmn` branch:

```python
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
```

`_finish_add` uses the query text as both the search's name and its query — matching typed `/add <name>`'s single-arg behavior, where name defaults to the query, exactly as the spec's Layer 2 section describes.

```python
# src/carouauto/telegram_listener.py — extend the pending branch inside handle_update,
# right after the existing "not text.startswith('/')" pending check:

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
```

This replaces the single `else: await _apply_pending_reply(...)` call from Task 8 with a three-way branch on `pending.kind`; `setprice`/`setexclude` still fall to `_apply_pending_reply` via the final `else`.

```python
# src/carouauto/telegram_listener.py — new function, alongside _apply_pending_reply
from .commands import _build_carousell_search_url, _finish_add, _is_url


async def _apply_pending_add_price(pending, text: str, chat_id: int, ctx: BotContext) -> None:
    parsed = _parse_two_optional_numbers(text)
    if parsed == "invalid":
        ctx.notifier.send_text(chat_id, "min and max must both be numbers, or reply `none`. Try again:")
        return  # pending state kept
    min_price, max_price = parsed
    del ctx.pending[chat_id]
    url = pending.query if _is_url(pending.query) else _build_carousell_search_url(pending.query)
    reply = _finish_add(chat_id, pending.query, url, min_price, max_price, ctx)
    ctx.notifier.send_text(chat_id, reply)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_callbacks.py tests/test_telegram_listener.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Run the full suite, commit, push**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS

```bash
git add src/carouauto/commands.py src/carouauto/callbacks.py src/carouauto/telegram_listener.py tests/test_callbacks.py tests/test_telegram_listener.py
git commit -m "feat: complete the guided /add flow with optional price range"
gh auth switch --hostname github.com --user ststheting
git push origin main
gh auth switch --hostname github.com --user hmishaun
```

**Layer 2 is now deployable.** `/add` with no arguments walks a user through adding a search with zero typed syntax. Typed `/add <name> [query] [min] [max]` and `/addurl` are unchanged. Deploy and confirm live before continuing.

---

## Layer 3: Notification cards

### Task 13: `send_photo` with a text fallback

**Files:**
- Modify: `src/carouauto/notifier.py`
- Test: `tests/test_notifier.py`

**Interfaces:**
- Produces: `TelegramNotifier.send_photo(chat_id, photo_url, caption, reply_markup) -> None`. If the photo POST fails, falls back to `send_text(chat_id, caption, reply_markup)`; only if that also fails does the call raise.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_notifier.py — add
def test_send_photo_posts_to_sendphoto_with_caption_and_markup():
    client = FakeClient()
    notifier = TelegramNotifier(BOT_TOKEN, client=client)
    markup = {"inline_keyboard": [[{"text": "Open", "url": "https://example.com"}]]}

    notifier.send_photo(CHAT_ID, "https://example.com/thumb.jpg", "A nice lamp\nS$50", markup)

    url, data, _ = client.posts[0]
    assert url == f"{TELEGRAM_API_BASE}/bot{BOT_TOKEN}/sendPhoto"
    assert data["chat_id"] == CHAT_ID
    assert data["photo"] == "https://example.com/thumb.jpg"
    assert data["caption"] == "A nice lamp\nS$50"
    assert json.loads(data["reply_markup"]) == markup


def test_send_photo_falls_back_to_text_when_the_photo_send_fails():
    class PhotoFailsClient:
        def __init__(self):
            self.posts = []

        def post(self, url, data=None, files=None):
            self.posts.append((url, data, files))
            if "sendPhoto" in url:
                raise httpx.ConnectError("photo fetch failed")
            return httpx.Response(200, request=httpx.Request("POST", url))

    notifier = TelegramNotifier(BOT_TOKEN, client=PhotoFailsClient())

    notifier.send_photo(CHAT_ID, "https://example.com/thumb.jpg", "A nice lamp", None)  # must not raise

    assert notifier._client.posts[0][0].endswith("/sendPhoto")
    assert notifier._client.posts[1][0].endswith("/sendMessage")
    assert notifier._client.posts[1][1]["text"] == "A nice lamp"


def test_send_photo_raises_only_if_both_photo_and_text_fallback_fail():
    class AlwaysFailsClient:
        def post(self, url, data=None, files=None):
            raise httpx.ConnectError(f"failed for {url}")

    notifier = TelegramNotifier(BOT_TOKEN, client=AlwaysFailsClient())

    with pytest.raises(RuntimeError) as excinfo:
        notifier.send_photo(CHAT_ID, "https://example.com/thumb.jpg", "A nice lamp", None)

    assert BOT_TOKEN not in str(excinfo.value)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_notifier.py -v -k send_photo`
Expected: FAIL — `AttributeError: 'TelegramNotifier' object has no attribute 'send_photo'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/carouauto/notifier.py — add after edit_message
    def send_photo(
        self, chat_id: int, photo_url: str, caption: str, reply_markup: dict | None
    ) -> None:
        url = f"{TELEGRAM_API_BASE}/bot{self._bot_token}/sendPhoto"
        data = {"chat_id": chat_id, "photo": photo_url, "caption": caption}
        if reply_markup is not None:
            data["reply_markup"] = json.dumps(reply_markup)
        try:
            response = self._client.post(url, data=data)
            response.raise_for_status()
            return
        except httpx.HTTPError:
            pass  # fall back to a text card below — a bad/unfetchable thumbnail shouldn't lose the notification
        self.send_text(chat_id, caption, reply_markup)
```

`send_text` already retries once and raises `RuntimeError` (never the raw httpx exception) on total failure, which is exactly the "raises only if both fail" behavior the test expects — no extra try/except needed here.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_notifier.py -v`
Expected: PASS (all tests, old and new)

- [ ] **Step 5: Commit**

```bash
git add src/carouauto/notifier.py tests/test_notifier.py
git commit -m "feat: add send_photo with a text fallback on failure"
```

---

### Task 14: Card-per-listing notifications, with a batched-text fallback for bursts

**Files:**
- Modify: `src/carouauto/notifier.py`
- Modify: `src/carouauto/scheduler.py`
- Modify: `src/carouauto/ui.py`
- Test: `tests/test_notifier.py`
- Test: `tests/test_scheduler.py`

**Interfaces:**
- Produces: `ui.notification_card_keyboard(search_id: int, url: str, hide_bumped: bool) -> dict` (Open listing as a URL button, Mute this search, Show/Hide bumped). `notifier.CARD_LIMIT = 5`. `TelegramNotifier.send_new_listings(chat_id, search_id, search_name, listings, hide_bumped)` — signature gains `search_id` and `hide_bumped`; ≤`CARD_LIMIT` listings send one `send_photo` card each, more send the existing batched-text path unchanged.

**Behavior change:** `send_new_listings`'s signature changes (new required params). Its only call site is in `scheduler.py`; update it and the `FakeNotifier` test double in `test_scheduler.py` together in this task so the suite is never left broken mid-task.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_notifier.py — add
def make_listings(n):
    return [make_listing(i) for i in range(n)]


def test_send_new_listings_sends_one_card_per_listing_when_at_or_under_the_limit():
    client = FakeClient()
    notifier = TelegramNotifier(BOT_TOKEN, client=client)

    notifier.send_new_listings(CHAT_ID, 7, "speediance", make_listings(3), hide_bumped=True)

    photo_posts = [p for p in client.posts if p[0].endswith("/sendPhoto")]
    assert len(photo_posts) == 3
    _, data, _ = photo_posts[0]
    markup = json.loads(data["reply_markup"])
    flat = [b for row in markup["inline_keyboard"] for b in row]
    assert any(b.get("url") == "https://www.carousell.sg/p/item-0/" for b in flat)
    assert any(b.get("callback_data") == "cmt:7" for b in flat)
    assert any(b.get("callback_data") == "cbb:7" for b in flat)


def test_send_new_listings_falls_back_to_batched_text_above_the_card_limit():
    client = FakeClient()
    notifier = TelegramNotifier(BOT_TOKEN, client=client)

    notifier.send_new_listings(CHAT_ID, 7, "speediance", make_listings(6), hide_bumped=True)

    photo_posts = [p for p in client.posts if p[0].endswith("/sendPhoto")]
    text_posts = [p for p in client.posts if p[0].endswith("/sendMessage")]
    assert photo_posts == []
    assert len(text_posts) == 1
    assert "6 new listings" in text_posts[0][1]["text"]


def test_send_new_listings_with_no_listings_sends_nothing():
    client = FakeClient()
    notifier = TelegramNotifier(BOT_TOKEN, client=client)

    notifier.send_new_listings(CHAT_ID, 7, "speediance", [], hide_bumped=True)

    assert client.posts == []
```

```python
# tests/test_scheduler.py — update FakeNotifier.send_new_listings and its call site's expectations
class FakeNotifier:
    def __init__(self, fail_for_chat_id=None):
        self.new_listings_calls = []  # (chat_id, search_id, search_name, listings, hide_bumped)
        self.alerts = []  # (chat_id, text)
        self._fail_for_chat_id = fail_for_chat_id

    def send_new_listings(self, chat_id, search_id, search_name, listings, hide_bumped):
        self.new_listings_calls.append((chat_id, search_id, search_name, listings, hide_bumped))
        if self._fail_for_chat_id == chat_id:
            raise RuntimeError("Telegram send failed after retry: HTTPError")

    def send_alert(self, chat_id, text):
        self.alerts.append((chat_id, text))
```

This changes the shape of `notifier.new_listings_calls` tuples from 3-element to 5-element. Update every existing unpacking site in `test_scheduler.py`:

```python
# every occurrence of:
#     chat_id, search_name, new_listings = notifier.new_listings_calls[0]
# becomes:
#     chat_id, search_id, search_name, new_listings, hide_bumped = notifier.new_listings_calls[0]
```

Grep for the exact list before editing: `grep -n "new_listings_calls\[0\]" tests/test_scheduler.py` — apply the same 5-tuple unpacking at every match. Also update the two multi-value-check sites that don't unpack (`assert len(notifier.new_listings_calls) == 1`) — those need no change.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_notifier.py tests/test_scheduler.py -v`
Expected: FAIL — `TypeError: send_new_listings() takes 4 positional arguments but 5 were given` (from `scheduler.py`'s still-old call site) and the new notifier tests failing with the same signature mismatch.

- [ ] **Step 3: Write minimal implementation**

```python
# src/carouauto/ui.py — append
def notification_card_keyboard(search_id: int, url: str, hide_bumped: bool) -> dict:
    bumped_label = "👁 Show bumped" if hide_bumped else "🙈 Hide bumped"
    return inline_keyboard(
        [
            [{"text": "Open listing", "url": url}],  # url buttons pass straight through, not via inline_keyboard's callback_data path
        ]
    )
```

`inline_keyboard`'s tuples are `(text, callback_data)` — a URL button doesn't fit that shape, so build this one directly rather than forcing it through `inline_keyboard`:

```python
# src/carouauto/ui.py — replace the function above with this
def notification_card_keyboard(search_id: int, url: str, hide_bumped: bool) -> dict:
    bumped_label = "👁 Show bumped" if hide_bumped else "🙈 Hide bumped"
    return {
        "inline_keyboard": [
            [{"text": "Open listing", "url": url}],
            [
                {"text": "🔇 Mute this search", "callback_data": f"cmt:{search_id}"},
                {"text": bumped_label, "callback_data": f"cbb:{search_id}"},
            ],
        ]
    }
```

```python
# src/carouauto/notifier.py — add near the other module constants
CARD_LIMIT = 5
```

Replace `send_new_listings`:

```python
    def send_new_listings(
        self, chat_id: int, search_id: int, search_name: str, listings: list[Listing], hide_bumped: bool
    ) -> None:
        if not listings:
            return
        if len(listings) <= CARD_LIMIT:
            for listing in listings:
                caption = f"New listing for '{search_name}':\n\n{format_message(listing)}"
                markup = ui.notification_card_keyboard(search_id, listing.url, hide_bumped)
                if listing.thumbnail_url:
                    self.send_photo(chat_id, listing.thumbnail_url, caption, markup)
                else:
                    self.send_text(chat_id, caption, markup)
            return

        if len(listings) == 1:
            self._send_text(
                chat_id, f"New listing for '{search_name}':\n\n{format_message(listings[0])}"
            )
            return

        header = f"{len(listings)} new listings for '{search_name}':"
        prefix = f"{header}\n\n"
        chunk: list[str] = []
        chunk_len = len(prefix)
        for listing in listings:
            body = format_message(listing)
            added = len(body) + (2 if chunk else 0)
            if chunk and chunk_len + added > MAX_MESSAGE_CHARS:
                self._send_text(chat_id, prefix + "\n\n".join(chunk))
                prefix = ""
                chunk = [body]
                chunk_len = len(body)
            else:
                chunk.append(body)
                chunk_len += added
        if chunk:
            self._send_text(chat_id, prefix + "\n\n".join(chunk))
```

Add the import at the top of `notifier.py`:

```python
from . import ui
```

```python
# src/carouauto/scheduler.py — update the send_new_listings call site
                notifier.send_new_listings(sub.chat_id, sub.search_id, sub.name, filtered, sub.hide_bumped)
```

(This is inside `run_cycle_for_url`'s per-subscriber loop, replacing the existing `notifier.send_new_listings(sub.chat_id, sub.name, filtered)` call.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_notifier.py tests/test_scheduler.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Run the full suite, commit, push**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS

```bash
git add src/carouauto/notifier.py src/carouauto/scheduler.py src/carouauto/ui.py tests/test_notifier.py tests/test_scheduler.py
git commit -m "feat: send a photo card per listing for small bursts, batched text above 5"
gh auth switch --hostname github.com --user ststheting
git push origin main
gh auth switch --hostname github.com --user hmishaun
```

---

### Task 15: Card buttons — mute and toggle-bumped

**Files:**
- Modify: `src/carouauto/callbacks.py`
- Test: `tests/test_callbacks.py`

**Interfaces:**
- Produces: new verbs `cmt:<id>` (pause the search; the card's own keyboard is updated in place to show it's muted) and `cbb:<id>` (toggle `hide_bumped`; the card's keyboard label flips).

**Design note:** unlike panel verbs, these don't re-render a full panel — the message being edited is a notification card (a listing's title/price/url as text, no filter summary), so the result text stays the card's own caption; only the keyboard changes, plus a toast confirms the action.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_callbacks.py — add
@pytest.mark.asyncio
async def test_cmt_mutes_the_search_and_keeps_the_card_caption(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    search_id = ctx.subscriptions.add_search(111, "speediance", "https://example.com/s")

    result = await dispatch_callback(f"cmt:{search_id}", 111, ctx)

    assert ctx.subscriptions.get_search_by_id(111, search_id).paused is True
    assert "muted" in (result.toast or "").lower()


@pytest.mark.asyncio
async def test_cmt_on_a_stale_search_does_not_raise(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)

    result = await dispatch_callback("cmt:999999", 111, ctx)

    assert "no longer exists" in result.text.lower()


@pytest.mark.asyncio
async def test_cbb_toggles_hide_bumped_and_updates_the_card_keyboard(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    search_id = ctx.subscriptions.add_search(111, "speediance", "https://example.com/s")
    assert ctx.subscriptions.get_search_by_id(111, search_id).hide_bumped is True  # default

    result = await dispatch_callback(f"cbb:{search_id}", 111, ctx)

    assert ctx.subscriptions.get_search_by_id(111, search_id).hide_bumped is False
    flat = [b for row in result.reply_markup["inline_keyboard"] for b in row]
    bumped_button = next(b for b in flat if b.get("callback_data") == f"cbb:{search_id}")
    assert "hide" in bumped_button["text"].lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_callbacks.py -v -k "cmt or cbb"`
Expected: FAIL — `cmt`/`cbb` fall through to the "unknown verb" branch, so `result.toast` stays `"Unknown action"` and no state changes.

- [ ] **Step 3: Write minimal implementation**

Add two branches to `dispatch_callback`, after the `afc` branch:

```python
        if verb == "cmt":
            sub = ctx.subscriptions.get_search_by_id(chat_id, int(parts[1]))
            if sub is None:
                return _stale_search_result()
            ctx.subscriptions.set_paused(chat_id, sub.name, True)
            return CallbackResult(
                text=CARD_TEXT_UNCHANGED,
                reply_markup=ui.notification_card_keyboard(sub.search_id, sub.url, sub.hide_bumped),
                toast=f"Muted '{sub.name}'.",
            )

        if verb == "cbb":
            sub = ctx.subscriptions.get_search_by_id(chat_id, int(parts[1]))
            if sub is None:
                return _stale_search_result()
            ctx.subscriptions.set_hide_bumped(chat_id, sub.name, not sub.hide_bumped)
            updated = ctx.subscriptions.get_search_by_id(chat_id, sub.search_id)
            return CallbackResult(
                text=CARD_TEXT_UNCHANGED,
                reply_markup=ui.notification_card_keyboard(updated.search_id, updated.url, updated.hide_bumped),
                toast="Updated.",
            )
```

`sub.url` here is the *search's* URL (its Carousell search-results page), not the individual listing's URL the card originally linked to — `UserSearch` has no per-listing URL to recover once the card has been sent, since the callback data only carries `search_id`. Preserve the listing's own "Open listing" link by keeping the card's existing keyboard's URL button rather than rebuilding it from scratch:

```python
# src/carouauto/callbacks.py — replace both new branches with this shared helper + two callers
def _card_keyboard_with_updated_bumped_label(existing_markup: dict, search_id: int, hide_bumped: bool) -> dict:
    bumped_label = "👁 Show bumped" if hide_bumped else "🙈 Hide bumped"
    rows = [row[:] for row in existing_markup["inline_keyboard"]]
    for row in rows:
        for button in row:
            if button.get("callback_data") == f"cbb:{search_id}":
                button["text"] = bumped_label
    return {"inline_keyboard": rows}
```

This needs the *original* card's markup, which `dispatch_callback` doesn't currently receive — only `data`, `chat_id`, `ctx`. Extend the signature to accept it:

```python
# src/carouauto/callbacks.py — change the function signature
async def dispatch_callback(data: str, chat_id: int, ctx: BotContext, current_markup: dict | None = None) -> CallbackResult:
```

And use `current_markup` (falling back to rebuilding from scratch only if it's `None`, which no real Telegram update ever sends but keeps the function safe to call without it in tests that don't care):

```python
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
```

Define the sentinel near the top of `callbacks.py`, alongside `CallbackResult`:

```python
# A marker CallbackResult.text can be set to, meaning "leave the message's
# existing text alone — only the keyboard changed." editMessageText requires
# a text value, so the listener (Task-15-adjacent change below) substitutes
# the message's own current text instead of actually re-sending this string.
CARD_TEXT_UNCHANGED = "\x00__unchanged__\x00"
```

```python
# src/carouauto/telegram_listener.py — update the callback branch inside handle_update
        result = await dispatch_callback(data, chat_id, ctx, callback["message"].get("reply_markup"))
        text = callback["message"].get("text", "") if result.text == CARD_TEXT_UNCHANGED else result.text
        try:
            ctx.notifier.edit_message(chat_id, message_id, text, result.reply_markup)
```

Add `from .callbacks import CARD_TEXT_UNCHANGED, dispatch_callback` to `telegram_listener.py`'s imports (replacing the plain `dispatch_callback` import).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_callbacks.py tests/test_telegram_listener.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Run the full suite, commit, push**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS

```bash
git add src/carouauto/callbacks.py src/carouauto/telegram_listener.py tests/test_callbacks.py
git commit -m "feat: mute and toggle-bumped buttons on notification cards"
gh auth switch --hostname github.com --user ststheting
git push origin main
gh auth switch --hostname github.com --user hmishaun
```

**Layer 3 is now deployable.** New listings up to 5 per poll arrive as photo cards with working buttons; bursts above that fall back to today's batched text untouched. Deploy and confirm live before continuing.

---

## Layer 4: Discovery

### Task 16: Admin-only commands hidden from regular users' menu

**Files:**
- Modify: `src/carouauto/telegram_listener.py`
- Test: `tests/test_telegram_listener.py`

**Interfaces:**
- Produces: `telegram_listener.DEFAULT_BOT_COMMANDS` (the existing `BOT_COMMANDS` list minus `revoke`/`backup`), `telegram_listener.ADMIN_BOT_COMMANDS` (the full list, unchanged). `set_bot_commands(client, bot_token, admin_chat_id)` registers `DEFAULT_BOT_COMMANDS` with no scope and `ADMIN_BOT_COMMANDS` scoped to `BotCommandScopeChat` for `admin_chat_id`.

**Note:** this is a client-side menu affordance only, exactly like the comment already on `BOT_COMMANDS` says — `dispatch()`'s existing server-side `_ADMIN_COMMANDS` check (already in `commands.py`, untouched by this task) remains the actual authority.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_telegram_listener.py — add
from carouauto.telegram_listener import ADMIN_BOT_COMMANDS, DEFAULT_BOT_COMMANDS, set_bot_commands


def test_default_commands_exclude_admin_only_ones():
    names = {c["command"] for c in DEFAULT_BOT_COMMANDS}
    assert "revoke" not in names
    assert "backup" not in names
    assert "start" in names  # regular commands still present


def test_admin_commands_include_everything():
    names = {c["command"] for c in ADMIN_BOT_COMMANDS}
    assert "revoke" in names
    assert "backup" in names


@pytest.mark.asyncio
async def test_set_bot_commands_registers_default_and_admin_scoped_lists():
    posted = []

    class FakeAsyncClient:
        async def post(self, url, json=None, timeout=None):
            posted.append((url, json))
            return httpx.Response(200, request=httpx.Request("POST", url))

    await set_bot_commands(FakeAsyncClient(), "tok", admin_chat_id=42)

    assert len(posted) == 2
    default_call = next(p for p in posted if "scope" not in p[1])
    admin_call = next(p for p in posted if "scope" in p[1])
    assert default_call[1]["commands"] == DEFAULT_BOT_COMMANDS
    assert admin_call[1]["commands"] == ADMIN_BOT_COMMANDS
    assert admin_call[1]["scope"] == {"type": "chat", "chat_id": 42}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_telegram_listener.py -v -k "commands or set_bot_commands"`
Expected: FAIL — `ImportError: cannot import name 'DEFAULT_BOT_COMMANDS'`; `set_bot_commands` doesn't take `admin_chat_id` yet.

- [ ] **Step 3: Write minimal implementation**

```python
# src/carouauto/telegram_listener.py — replace the BOT_COMMANDS block
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
```

Update `run_command_listener`'s call site and its caller in `main.py`:

```python
# src/carouauto/telegram_listener.py — inside run_command_listener
async def run_command_listener(bot_token: str, ctx: BotContext, admin_chat_id: int) -> None:
    offset: int | None = None
    async with httpx.AsyncClient() as client:
        try:
            await set_bot_commands(client, bot_token, admin_chat_id)
        except httpx.HTTPError as exc:
            logger.error("failed to set bot command menu: %s", type(exc).__name__)
        # ... rest unchanged ...
```

```python
# src/carouauto/main.py — update the run_command_listener call
            run_command_listener(config.telegram_bot_token, ctx, admin_chat_id),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_telegram_listener.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Run the full suite, commit, push**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS — also check `grep -rn "run_command_listener(" src/carouauto tests` to confirm every call site (including `main.py`) was updated; the search should show exactly `telegram_listener.py`'s definition and `main.py`'s one call site.

```bash
git add src/carouauto/telegram_listener.py src/carouauto/main.py tests/test_telegram_listener.py
git commit -m "feat: hide admin-only commands from regular users' Telegram menu"
gh auth switch --hostname github.com --user ststheting
git push origin main
gh auth switch --hostname github.com --user hmishaun
```

---

### Task 17: Main menu on `/start` and `/help`

**Files:**
- Modify: `src/carouauto/commands.py`
- Modify: `src/carouauto/ui.py`
- Modify: `src/carouauto/callbacks.py`
- Test: `tests/test_commands.py`
- Test: `tests/test_callbacks.py`

**Interfaces:**
- Produces: `ui.main_menu_keyboard() -> dict` (buttons `mm:se`, `mm:ad`, `mm:st`, `mm:he`). `handle_start` and `handle_help` send this keyboard via `ctx.notifier` and return `""`, following the same pattern as `handle_searches` (Task 5). New callback verbs `mm:se` / `mm:ad` / `mm:st` / `mm:he`, reusing `_searches_list_result`, the guided-add prompt, and the existing status/help text.

**Behavior change:** `handle_start` and `handle_help` no longer return their text directly — update the existing tests.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_commands.py — replace test_handle_start_mentions_register and test_handle_help_mentions_the_poll_interval
@pytest.mark.asyncio
async def test_handle_start_sends_the_main_menu(tmp_path):
    ctx = make_ctx(tmp_path)
    sent = []

    class FakeNotifier:
        def send_text(self, chat_id, text, reply_markup=None):
            sent.append((chat_id, text, reply_markup))

    ctx.notifier = FakeNotifier()

    reply = await handle_start([], 111, ctx)

    assert reply == ""
    chat_id, text, reply_markup = sent[0]
    assert "/register" in text
    flat = [b["callback_data"] for row in reply_markup["inline_keyboard"] for b in row]
    assert flat == ["mm:se", "mm:ad", "mm:st", "mm:he"]


@pytest.mark.asyncio
async def test_handle_help_sends_the_main_menu(tmp_path):
    ctx = make_ctx(tmp_path)
    sent = []

    class FakeNotifier:
        def send_text(self, chat_id, text, reply_markup=None):
            sent.append((chat_id, text, reply_markup))

    ctx.notifier = FakeNotifier()

    reply = await handle_help([], 111, ctx)

    assert reply == ""
    chat_id, text, reply_markup = sent[0]
    assert "5 minute" in text.lower()
    assert reply_markup["inline_keyboard"][0][0]["callback_data"] == "mm:se"
```

```python
# tests/test_callbacks.py — add
@pytest.mark.asyncio
async def test_mm_se_shows_the_searches_list(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    ctx.subscriptions.add_search(111, "speediance", "https://example.com/s")

    result = await dispatch_callback("mm:se", 111, ctx)

    assert "speediance" in result.text


@pytest.mark.asyncio
async def test_mm_ad_starts_the_guided_add_flow(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)

    result = await dispatch_callback("mm:ad", 111, ctx)

    assert result.force_reply_prompt is not None
    assert ctx.pending[111].kind == "add_query"


@pytest.mark.asyncio
async def test_mm_st_shows_status(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)
    ctx.subscriptions.add_search(111, "speediance", "https://example.com/s")

    result = await dispatch_callback("mm:st", 111, ctx)

    assert "speediance" in result.text
    assert "poll interval" in result.text.lower()


@pytest.mark.asyncio
async def test_mm_he_shows_help_text(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.subscriptions.register(111)

    result = await dispatch_callback("mm:he", 111, ctx)

    assert "/register" in result.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_commands.py tests/test_callbacks.py -v -k "start or help or mm_"`
Expected: FAIL — `handle_start`/`handle_help` still just return text; `mm:*` verbs don't exist.

- [ ] **Step 3: Write minimal implementation**

```python
# src/carouauto/ui.py — append
def main_menu_keyboard() -> dict:
    return inline_keyboard(
        [
            [("🔎 My searches", "mm:se"), ("➕ Add search", "mm:ad")],
            [("📊 Status", "mm:st"), ("❓ Help", "mm:he")],
        ]
    )
```

```python
# src/carouauto/commands.py — replace handle_start and handle_help
async def handle_start(args: list[str], chat_id: int, ctx: BotContext) -> str:
    text = (
        "Welcome to carouauto — a Carousell listing monitor.\n"
        "Send /register <password> to get started, then /help for what you can do."
    )
    ctx.notifier.send_text(chat_id, text, reply_markup=ui.main_menu_keyboard())
    return ""


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
        "/revoke <chat_id> — admin only, remove someone's access\n"
        "/backup — admin only, get a copy of the database\n"
        "/help — this message"
    )
    ctx.notifier.send_text(chat_id, text, reply_markup=ui.main_menu_keyboard())
    return ""
```

```python
# src/carouauto/callbacks.py — new imports
from .commands import handle_help, handle_start, handle_status
```

Add four branches to `dispatch_callback`, after the `cbb` branch:

```python
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
```

`handle_status` still returns its text directly (it was never changed to a notifier-side-effect handler), so calling it from here and reusing its return value works without any change to `commands.py` beyond what Task 17 already made. The `mm:he` branch intentionally doesn't call `handle_help` — `handle_help` was just changed (this task, above) to send via `ctx.notifier` and return `""`, which would produce an empty `CallbackResult.text`; a short inline summary here avoids that trap. Remove the unused `handle_help, handle_start` names from the import line added above, keeping only `handle_status`:

```python
# src/carouauto/callbacks.py — correct the import to only what's used
from .commands import handle_status
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_commands.py tests/test_callbacks.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Run the full suite, commit, push**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS

```bash
git add src/carouauto/commands.py src/carouauto/ui.py src/carouauto/callbacks.py tests/test_commands.py tests/test_callbacks.py
git commit -m "feat: main menu keyboard on /start and /help"
gh auth switch --hostname github.com --user ststheting
git push origin main
gh auth switch --hostname github.com --user hmishaun
```

**Layer 4 is now deployable — the full spec is implemented.** `/start` and `/help` open a main menu; every command has a server-side admin check backed by a client-side menu that hides admin-only commands from regular users. Deploy and confirm live.

---

## Self-Review Notes

- **Spec coverage:** Layer 0 (Task 1-5) ✓, Layer 1 (Task 6-10) ✓, Layer 2 (Task 11-12) ✓, Layer 3 (Task 13-15) ✓, Layer 4 (Task 16-17) ✓. Ownership/staleness checks (Task 4), pending-input expiry (Task 8), "message is not modified" swallowing (Task 2), photo-failure fallback (Task 13), CARD_LIMIT batching (Task 14) — every spec section has a task.
- **Callback data verbs, final list:** `ls`, `sp:<id>`, `tg:<id>:pa`, `tg:<id>:hb`, `cd:<id>`, `cds:<id>:<idx>`, `pp:<id>`, `px:<id>`, `rm:<id>`, `rmy:<id>`, `rmn:<id>`, `afy`, `afp`, `afc`, `cmt:<id>`, `cbb:<id>`, `mm:se`, `mm:ad`, `mm:st`, `mm:he` — all well under 64 bytes; the longest (`cds:<id>:<idx>` with a large id) is covered by Task 1's generic length assertion, which every `ui.py` builder passes through.
- **Type/name consistency checked:** `PendingInput` (Task 7) gains `query` in Task 12, not redefined — Task 12's code block edits the same class. `CallbackResult` (Task 4) gains no new fields after its initial definition (`force_reply_prompt` was already present from Task 4 onward, used starting Task 7). `send_new_listings`'s new signature (Task 14) is used consistently by its one caller (`scheduler.py`) and its one test double (`FakeNotifier` in `test_scheduler.py`), both updated in the same task. `dispatch_callback`'s signature gains `current_markup` in Task 15 — Task 15 is also where the listener call site is updated to pass it, so no task in between calls the 3-arg form after Task 15 lands.
- **No deployment steps in this plan** — deploying to the VPS (git pull, service restart, log verification) is a manual follow-up after each layer's tasks are merged, following the same steps used for every prior deploy in this project. Not included here since it's operational, not implementation.
