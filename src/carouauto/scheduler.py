from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable

from .challenge import is_challenge_page
from .db import SeenStore
from .parser import parse_listings

FetchHtmlFn = Callable[[str], Awaitable[str]]

CHALLENGE_REMINDER_AFTER_SECONDS = 30 * 60


@dataclass
class SearchState:
    paused: bool = False
    paused_since: datetime | None = None
    reminder_sent: bool = False


async def run_cycle(
    search_name: str,
    search_url: str,
    state: SearchState,
    fetch_html: FetchHtmlFn,
    seen_store: SeenStore,
    notifier,
    tunnel_instructions: str,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> None:
    html = await fetch_html(search_url)

    if is_challenge_page(html):
        if not state.paused:
            state.paused = True
            state.paused_since = now()
            state.reminder_sent = False
            notifier.send_alert(
                f"Cloudflare challenge is blocking '{search_name}'. Solve it manually:\n{tunnel_instructions}"
            )
        elif not state.reminder_sent and state.paused_since is not None:
            elapsed = (now() - state.paused_since).total_seconds()
            if elapsed > CHALLENGE_REMINDER_AFTER_SECONDS:
                state.reminder_sent = True
                notifier.send_alert(f"Still waiting on a manual challenge solve for '{search_name}'.")
        return

    if state.paused:
        state.paused = False
        state.paused_since = None
        state.reminder_sent = False

    listings = parse_listings(html)
    new_ids = seen_store.diff_and_update(search_name, [l.listing_id for l in listings])
    new_listings = [l for l in listings if l.listing_id in new_ids]
    if new_listings:
        notifier.send_new_listings(search_name, new_listings)


async def run_forever(
    searches: list[tuple[str, str]],
    fetch_html: FetchHtmlFn,
    seen_store: SeenStore,
    notifier,
    tunnel_instructions: str,
    poll_interval_seconds: float,
    poll_jitter_fraction: float,
) -> None:
    states = {name: SearchState() for name, _ in searches}
    while True:
        for name, url in searches:
            try:
                await run_cycle(name, url, states[name], fetch_html, seen_store, notifier, tunnel_instructions)
            except Exception as exc:
                print(f"[carouauto] error polling '{name}': {exc}")
        jitter = poll_interval_seconds * poll_jitter_fraction
        await asyncio.sleep(poll_interval_seconds + random.uniform(-jitter, jitter))
