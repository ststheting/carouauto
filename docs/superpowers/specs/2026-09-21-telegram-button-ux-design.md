# Telegram Button UX — Design

## Goal

Make the bot usable without typing commands, quoted names, or numeric arguments, by using more of the Telegram Bot API: inline keyboards, callback queries, in-place message editing, ForceReply prompts, photo messages, and command scopes. Typed commands keep working as a fallback.

## Non-goals

Mini App / web UI, translations, group chats, inline mode.

## Layers (each is its own plan and deploy, in this order)

0. Foundation (callback plumbing)
1. Search panels
2. Guided add
3. Notification cards
4. Discovery (command scopes, main menu)

No schema migration: everything uses existing columns.

## Layer 0 — Foundation

- New `ui.py`: pure functions that build keyboards (`InlineKeyboardMarkup` dicts) and panel text. No I/O.
- New `callbacks.py`: `dispatch_callback(data, chat_id, message_id, ctx) -> CallbackResult`, where a result carries optional new text, optional new keyboard, and an optional toast string. `commands.py` does not grow.
- `TelegramNotifier` gains: `send_text(..., reply_markup=None)`, `edit_message(chat_id, message_id, text, reply_markup)`, `answer_callback(callback_query_id, text=None)`, `send_photo(chat_id, photo_url, caption, reply_markup)`. All follow the existing rule: only the exception class name escapes; the bot token never appears in a message, log, or chained exception.
- `telegram_listener`: `getUpdates` uses `allowed_updates=["message","callback_query"]`. The per-update logic moves into a testable `handle_update(update, ctx)`. Every callback query is answered with `answerCallbackQuery` (stops the client spinner) even on failure.
- Callback data is compact and stateless: `<verb>:<search_id>[:<arg>]`, e.g. `sp:12` (open panel), `tg:12:hb` (toggle hide-bumped), `tg:12:pa` (toggle pause), `rm:12` (confirm prompt), `rmy:12` (confirmed), `cd:12:<n>` (set condition). Always ≤ 64 bytes (Telegram's limit); a `ui` test asserts this for every generated button.
- Security: callback data is untrusted. Every callback re-checks `is_active(chat_id)` and resolves the search via a new `SubscriptionStore.get_search_by_id(chat_id, search_id)` that filters on the owning chat, so a forged id cannot touch another user's search.
- Stale buttons: search no longer exists → toast + edit the message to say so. Telegram's "message is not modified" error on a no-op edit is swallowed.

## Layer 1 — Search panels

- `/searches` renders one button per search. Tapping opens a panel that edits the same message and shows current filters.
- Panel buttons: Pause/Resume, Hide/Show bumped, condition (one button per known value: Brand new, Like new, Lightly used, Well used, Heavily used, plus Any), Set price, Exclude words, Remove (with an "Are you sure?" step), Back.
- Set price and Exclude words use a ForceReply prompt ("Reply with min and max, e.g. `100 500`, or `none`"). Replies are matched via `BotContext.pending: dict[int, PendingInput]` (kind, search_id, created_at), expiring after 10 minutes and lost on restart (user just taps again). A non-command message with no live pending entry is ignored, as today.
- Panel actions call the same store methods the commands use.

## Layer 2 — Guided add

- `/add` with no args sends a ForceReply "What should I search for?". After the reply, inline buttons: Add now / Set price range first / Cancel. Name defaults to the query. `_finish_add` is reused, so the "hidden by default" note still appears.
- Typed `/add <name> [query] [min] [max]` and `/addurl` are unchanged.

## Layer 3 — Notification cards

- Up to `CARD_LIMIT = 5` new listings for a subscriber in one poll: each is a photo card (thumbnail, caption with title/price/age and the bumped badge) with buttons Open listing (URL button), Mute this search, Show/Hide bumped. Tapping a state button updates that card's own keyboard in place.
- More than `CARD_LIMIT`: existing compact batched text, unchanged.
- If a photo send fails (Telegram cannot fetch the image, or no thumbnail): fall back to a text card with the same buttons. Only if the text fallback also fails does the send raise.
- Known edge case: cards are sent one at a time and a search is marked seen only after all sends succeed, so a total failure on card N re-sends cards 1..N-1 next cycle. This is the existing at-least-once behavior; no per-card seen tracking.

## Layer 4 — Discovery

- Command scopes: `setMyCommands` registers a default list (regular commands only) and a chat-scoped list for the admin's chat that includes `/revoke` and `/backup`. Telegram does not report the scope on updates, so the server-side admin check remains authoritative.
- `/start` sends a welcome plus an inline main menu: My searches, Add search, Status, Help. `/help` opens with the same menu.

## Error handling

- Every callback path is wrapped like `dispatch`: it never raises to the listener loop, logs only the exception class name, and still answers the callback query.
- Expired pending input → treated as no pending input.
- A failed `edit_message` (other than "not modified") falls back to sending a fresh message.

## Testing

- `ui.py`: keyboard shapes, callback data ≤ 64 bytes for every button, panel text reflects filters.
- `callbacks.py`: real `SubscriptionStore`/`SeenStore` in `tmp_path`, fake notifier recording calls; ownership check (other user's search id is rejected), stale-button handling, each toggle, remove confirmation, pending-input flow and expiry.
- `handle_update`: routing of message vs callback_query updates; callback is always answered.
- Notifier: new methods via the existing fake HTTP client, including token-safety of error paths and photo-failure fallback.
- Command scopes: payloads for default and admin-chat registration.

## Rollout

One deploy per layer, in order, so a bug in one layer never blocks the earlier ones. No migration, so rollback is a plain `git` revert plus service restart.
