from __future__ import annotations

import httpx

from .models import Listing

TELEGRAM_API_BASE = "https://api.telegram.org"


def format_message(listing: Listing) -> str:
    lines = [listing.title]
    if listing.price:
        lines.append(listing.price)
    if listing.posted_text:
        lines.append(listing.posted_text)
    lines.append(listing.url)
    return "\n".join(lines)


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str, client: httpx.Client | None = None):
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._client = client or httpx.Client(timeout=10.0)

    def send_new_listings(self, search_name: str, listings: list[Listing]) -> None:
        if not listings:
            return
        if len(listings) == 1:
            text = f"New listing for '{search_name}':\n\n{format_message(listings[0])}"
        else:
            bodies = "\n\n".join(format_message(l) for l in listings)
            text = f"{len(listings)} new listings for '{search_name}':\n\n{bodies}"
        self._send_text(text)

    def send_alert(self, text: str) -> None:
        self._send_text(text)

    def _send_text(self, text: str) -> None:
        url = f"{TELEGRAM_API_BASE}/bot{self._bot_token}/sendMessage"
        response = self._client.post(url, data={"chat_id": self._chat_id, "text": text})
        response.raise_for_status()
