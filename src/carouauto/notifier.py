from __future__ import annotations

import json
import time

import httpx

from . import ui
from .models import Listing

TELEGRAM_API_BASE = "https://api.telegram.org"

# Telegram's hard limit is ~4096 characters; stay under it with headroom.
MAX_MESSAGE_CHARS = 4000
RETRY_BACKOFF_SECONDS = 2
CARD_LIMIT = 5


def format_message(listing: Listing) -> str:
    lines = []
    if listing.is_bumped:
        lines.append("🔁 Bumped or stale (not a fresh post)")
    lines.append(listing.title)
    if listing.price:
        lines.append(listing.price)
    if listing.posted_text:
        lines.append(listing.posted_text)
    lines.append(listing.url)
    return "\n".join(lines)


class TelegramNotifier:
    def __init__(self, bot_token: str, client: httpx.Client | None = None):
        self._bot_token = bot_token
        self._client = client or httpx.Client(timeout=10.0)

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

        # Greedily pack listing bodies into messages that stay under Telegram's
        # size limit; the header goes on the first chunk only.
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

    def send_alert(self, chat_id: int, text: str) -> None:
        self._send_text(chat_id, text)

    def send_text(self, chat_id: int, text: str, reply_markup: dict | None = None) -> None:
        self._send_text(chat_id, text, reply_markup)

    def edit_message(
        self, chat_id: int, message_id: int, text: str, reply_markup: dict | None
    ) -> None:
        url = f"{TELEGRAM_API_BASE}/bot{self._bot_token}/editMessageText"
        data = {"chat_id": chat_id, "message_id": message_id, "text": text}
        if reply_markup is not None:
            data["reply_markup"] = json.dumps(reply_markup)
        failure_name = ""
        try:
            response = self._client.post(url, data=data)
        except httpx.HTTPError as e:
            # Only the exception's class name escapes. httpx's own message
            # embeds the request URL, which contains the bot token, so it
            # must never be interpolated, chained, or re-raised. Raising
            # outside this except block means __context__ is not set.
            failure_name = type(e).__name__
        if failure_name:
            raise RuntimeError(f"Telegram edit failed: {failure_name}")
        if response.status_code == 400 and "not modified" in response.text.lower():
            return  # editing to identical content — not a real failure
        try:
            response.raise_for_status()
        except httpx.HTTPError as e:
            failure_name = type(e).__name__
        if failure_name:
            raise RuntimeError(f"Telegram edit failed: {failure_name}")

    def edit_reply_markup(self, chat_id: int, message_id: int, reply_markup: dict | None) -> None:
        url = f"{TELEGRAM_API_BASE}/bot{self._bot_token}/editMessageReplyMarkup"
        data = {"chat_id": chat_id, "message_id": message_id}
        if reply_markup is not None:
            data["reply_markup"] = json.dumps(reply_markup)
        failure_name = ""
        try:
            response = self._client.post(url, data=data)
        except httpx.HTTPError as e:
            # Only the exception's class name escapes. httpx's own message
            # embeds the request URL, which contains the bot token, so it
            # must never be interpolated, chained, or re-raised. Raising
            # outside this except block means __context__ is not set.
            failure_name = type(e).__name__
        if failure_name:
            raise RuntimeError(f"Telegram edit failed: {failure_name}")
        if response.status_code == 400 and "not modified" in response.text.lower():
            return  # editing to identical content — not a real failure
        try:
            response.raise_for_status()
        except httpx.HTTPError as e:
            failure_name = type(e).__name__
        if failure_name:
            raise RuntimeError(f"Telegram edit failed: {failure_name}")

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

    def send_document(self, chat_id: int, file_path: str, filename: str) -> None:
        url = f"{TELEGRAM_API_BASE}/bot{self._bot_token}/sendDocument"
        failure_name = ""
        with open(file_path, "rb") as f:
            files = {"document": (filename, f)}
            try:
                response = self._client.post(url, data={"chat_id": chat_id}, files=files)
                response.raise_for_status()
            except httpx.HTTPError as e:
                # Only the exception's class name escapes. httpx's own message
                # embeds the request URL, which contains the bot token, so it
                # must never be interpolated, chained, or re-raised. Raising
                # outside this except block (after the file is closed) means
                # no exception is "currently being handled" at raise time, so
                # __context__ ends up genuinely None, not just suppressed.
                failure_name = type(e).__name__
        if failure_name:
            raise RuntimeError(f"Telegram document send failed: {failure_name}")

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
