from __future__ import annotations

import time

import httpx

from .models import Listing

TELEGRAM_API_BASE = "https://api.telegram.org"

# Telegram's hard limit is ~4096 characters; stay under it with headroom.
MAX_MESSAGE_CHARS = 4000
RETRY_BACKOFF_SECONDS = 2


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

    def send_new_listings(self, chat_id: int, search_name: str, listings: list[Listing]) -> None:
        if not listings:
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

    def send_text(self, chat_id: int, text: str) -> None:
        self._send_text(chat_id, text)

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

    def _send_text(self, chat_id: int, text: str) -> None:
        url = f"{TELEGRAM_API_BASE}/bot{self._bot_token}/sendMessage"
        failure_name = ""
        for attempt in range(2):  # one initial attempt plus one retry
            try:
                response = self._client.post(url, data={"chat_id": chat_id, "text": text})
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
