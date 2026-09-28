# Telegram UX Round 2 — Design

## Goal

Stop making users remember and type search names, and make every common action reachable by tapping. Builds on the button UX shipped in `2026-09-21-telegram-button-ux-design.md` (callbacks.py / ui.py / pending-input machinery). Typed commands with full arguments keep working exactly as today.

## What the user asked for (verbatim intent)

- Name-taking commands (`/remove` and similar) should show the user's searches to pick from instead of requiring a separate lookup.
- Broader UX improvements, scoped by the user to four areas: search pickers, a richer search panel, smarter notification cards, and an always-visible bottom keyboard.
- Picker taps perform the action directly (user's explicit choice over "open the panel").

## Non-goals

Fuzzy/partial name matching (the picker covers mistyped names), renaming searches, price presets, quiet hours/digests, group chats, Mini Apps.

No schema change.

## 1. Plumbing

`CallbackResult` (callbacks.py) gains two optional fields, both defaulting so every existing construction site is unchanged:

- `send_as_new: bool = False` — the listener sends `result.text`/`result.reply_markup` as a fresh message via `send_text` instead of editing the tapped message. Needed because a photo notification card cannot be edited into a text panel.
- `followup: Callable[[], Awaitable[None]] | None = None` — an async step the listener awaits **after** `answer_callback`. Used for slow work (fetching current listings through the shared browser) so the button is answered immediately and Telegram never reports "query is too old".

Listener order for a callback becomes: dispatch → edit (or send, if `send_as_new`) with the existing fallback → `answer_callback` → force-reply prompt → followup. Each step after dispatch is independently guarded (log `type(exc).__name__` only), matching the existing token-safety rule.

## 2. Search pickers

- `ui.search_picker_keyboard(subs, verb) -> dict`: one button per search, callback data `f"{verb}:{search_id}"`, built through `inline_keyboard()` so the 64-byte guard applies.
- Shared helper in commands.py (`_send_picker(chat_id, ctx, verb, prompt)`) sends the picker via `ctx.notifier.send_text` and returns `""` (the established notifier-side-effect pattern). With zero searches it sends the existing "You have no tracked searches yet…" text instead.
- Entry points and triggers:

| Command | Picker shown when | Picker verb (tap does) |
|---|---|---|
| `/remove` | no name, or unknown name | `rm` — existing "Are you sure?" |
| `/pause` | no name, or unknown name | `pa` — pause now, with Undo |
| `/resume` | no name, or unknown name | `re` — resume now, with Undo |
| `/hidebumped` | no name, or unknown name | `hb` — hide bumped now, with Undo |
| `/showbumped` | no name, or unknown name | `sb` — show bumped now, with Undo |
| `/list` | no name, or unknown name | `li` — current listings (via followup) |
| `/setprice` | no name, or unknown name | `pp` — existing price ForceReply |
| `/setexclude` | no name, or unknown name | `px` — existing exclude ForceReply |
| `/setcondition` | no name, or unknown name | `cd` — existing condition picker |

- Unknown-name prompt text: `No search named '<x>' — pick one:`. No-name prompt: `Which search?`.
- `/setprice <name>`, `/setexclude <name>`, `/setcondition <name>` with a valid name but missing values jump straight to that search's prompt/picker (same results as tapping `pp`/`px`/`cd`), instead of a usage error. Full-argument forms are unchanged.
- New callback verbs (all resolve the search through `get_search_by_id(chat_id, search_id)` — the existing ownership rule):
  - `pa:<id>` / `re:<id>` — set `paused` explicitly (not a toggle). Result text `Paused '<name>'.` / `Resumed '<name>'.`, keyboard with one **Undo** button pointing at the opposite verb.
  - `hb:<id>` / `sb:<id>` — set `hide_bumped` explicitly, same Undo pattern.
  - `li:<id>` — result text `Fetching current listings for '<name>'…` (edits the picker/panel message), plus a `followup` that fetches the search URL via `ctx.fetch_html`, handles the Cloudflare-challenge and empty cases with the same texts as `/list`, and sends the listing text (existing `LIST_CAP`, existing `format_message`) as a new message. The listing-text builder is extracted from `handle_list` so `/list` and `li` share it.

## 3. Richer panel

- `ui.status_line(sub, state) -> str`: pure helper returning `paused (by you)`, `paused (Cloudflare challenge, awaiting manual solve)`, `last checked HH:MM SGT`, or `not yet checked`. `DISPLAY_TIMEZONE` moves from commands.py to ui.py (commands.py imports it from ui — dependency direction stays ui ← commands). `/status` uses the helper; output text unchanged.
- `ui.search_panel_text(sub, state=None)` appends `Status: <status_line>`, replacing today's `Status: paused/active` line. Every caller that has `ctx` passes `ctx.states.get(sub.url)`.
- `ui.search_panel_keyboard` gains a **👀 Current listings** button (`li:<id>`).
- `ui.condition_keyboard` prefixes the active condition's button with `✓ ` (or `✓ Any` when none is set).
- After adding a search (typed `/add`, `/addurl`, guided `afy`, guided add-price reply): the user gets the new search's panel with the header `Added '<name>'. Checks run every ~<N> minutes. Bumped/stale listings are hidden by default — change it below.` (where `<N>` is `round(ctx.poll_interval_seconds / 60)`, as in today's add confirmation) `_finish_add` is split so callers get the new `search_id` (or the duplicate-name error text) and render the panel themselves; typed handlers send it via notifier and return `""`, `afy` returns it as its `CallbackResult`.

## 4. Smarter notification cards

- `ui.notification_card_keyboard(search_id, url, hide_bumped, paused=False)` adds a **⚙️ Manage search** button (`ms:<id>`) and labels the mute button `🔇 Mute this search` / `🔔 Unmute` from `paused`. New cards are always sent unmuted (paused searches are not polled), so notifier call sites need no change.
- `cmt:<id>` becomes a toggle: flips `paused`, relabels the mute button in the card's existing keyboard (same copy-and-relabel approach `cbb` already uses — never rebuilds, so the per-listing Open URL survives), toast `Muted '<name>'.` / `Unmuted '<name>'.`.
- `ms:<id>` returns the search's panel with `send_as_new=True`, leaving the card untouched.
- Known limitation (accepted): while muted, no new cards arrive, so existing cards and the panel's Resume are the ways to unmute.

## 5. Always-visible bottom keyboard

- `ui.MENU_BUTTONS`: ordered mapping of label → command name — `🔎 My searches` → `searches`, `➕ Add search` → `add`, `📊 Status` → `status`, `❓ Help` → `help`.
- `ui.bottom_keyboard() -> dict`: `ReplyKeyboardMarkup` `{"keyboard": [[My searches, Add search], [Status, Help]], "is_persistent": true, "resize_keyboard": true}` (buttons as `{"text": label}`).
- Sent on: successful `/register` (the registration reply carries it); `/start` and `/help` when the user is already registered. A message can carry only one `reply_markup`, so:
  - `/start`: for registered users, sends the welcome with the bottom keyboard (the inline main menu is dropped — it duplicates the bottom keyboard). Unregistered users get the welcome text only (telling them to `/register`).
  - `/help`: keeps its inline main menu on the help message; for registered users additionally sends a one-line `Menu buttons are below the chat box.` message carrying the bottom keyboard.
- Listener routing: a non-command text message exactly matching a `MENU_BUTTONS` label is handled **before** pending-input handling. It clears any pending entry for that chat (the user navigated away) and routes through `dispatch(command, [], chat_id, ctx)` so the existing registration/admin gate applies unchanged.
- To verify live after deploy (not assumable from the docs): whether Telegram keeps the persistent keyboard visible while a ForceReply prompt is open. If it does not, a follow-up change re-sends it after the prompt is answered; out of scope unless observed.

## Error handling

- Pickers with zero searches → existing "no tracked searches" text, no keyboard.
- Every new verb: stale/foreign `search_id` → existing `_stale_search_result()`.
- `li` followup: fetch failure → sends `Couldn't fetch listings for '<name>' right now — try again shortly.`; logs `type(exc).__name__` only.
- Followup and send-as-new failures never propagate out of `handle_update` (the per-update loop guard from the last round remains the backstop).

## Testing

- `ui`: picker keyboard data format and ≤64 bytes; `status_line` for each state; panel includes `li`; ✓ on active condition and on Any; card keyboard Manage button, Mute vs Unmute labels; `bottom_keyboard` shape and label set.
- `callbacks`: `pa`/`re`/`hb`/`sb` set state explicitly and carry the correct Undo verb; `li` returns a followup that sends listings (fake `fetch_html`), and the challenge/empty/failure texts; `ms` returns `send_as_new`; `cmt` toggles and relabels without dropping the URL button; each new verb rejects a search owned by another chat.
- `commands`: each picker command with no name and with an unknown name sends a picker with the right verb; `/setprice <name>` etc. with a valid name trigger the prompt; add paths send the panel; `/start`/`/register`/`/help` send the bottom keyboard only to registered users.
- `telegram_listener`: menu-button text routes to the right command and clears pending state; `send_as_new` sends instead of editing; followup runs after `answer_callback`; a raising followup is contained.
- Real `SubscriptionStore`/`SeenStore` in `tmp_path`, fake notifier — same conventions as the existing suites.

## Rollout

Single deploy after the final whole-branch review: push, then **pull and restart on the VPS** (`/opt/carouauto`, `systemctl restart carouauto`), then check logs and try the flows in Telegram. No migration; rollback is a git revert plus restart.
