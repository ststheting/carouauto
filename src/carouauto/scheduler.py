from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable

from .challenge import is_challenge_page
from .db import SeenStore
from .parser import parse_listings

FetchHtmlFn = Callable[[str], Awaitable[str]]

CHALLENGE_REMINDER_AFTER_SECONDS = 30 * 60

# If every search fails for this many consecutive rounds, assume the browser is
# dead: alert, then exit so systemd's Restart=on-failure gives us a fresh one.
MAX_CONSECUTIVE_ROUND_FAILURES = 5

logger = logging.getLogger("carouauto")


class BrowserLikelyDeadError(RuntimeError):
    """Raised to exit the process so systemd restarts it with a fresh browser."""


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
        logger.info("search '%s': challenge detected, paused", search_name)
        return

    if state.paused:
        state.paused = False
        state.paused_since = None
        state.reminder_sent = False

    listings = parse_listings(html)
    all_ids = [l.listing_id for l in listings]
    new_ids = seen_store.get_new_ids(search_name, all_ids)
    new_listings = [l for l in listings if l.listing_id in new_ids]
    if new_listings:
        # If this raises, the exception propagates to run_one_round WITHOUT
        # anything being marked seen, so the same ids are retried next cycle.
        notifier.send_new_listings(search_name, new_listings)
    seen_store.mark_seen(search_name, all_ids)

    if new_listings:
        logger.info("search '%s': %d new listing(s)", search_name, len(new_listings))
    else:
        logger.info("search '%s': no new listings", search_name)


async def run_one_round(
    searches: list[tuple[str, str]],
    states: dict[str, SearchState],
    fetch_html: FetchHtmlFn,
    seen_store: SeenStore,
    notifier,
    tunnel_instructions: str,
) -> int:
    """Poll every search once. Returns how many succeeded.

    One search failing must never stop the others from being polled, so each
    cycle is individually guarded.
    """
    succeeded = 0
    for name, url in searches:
        try:
            await run_cycle(name, url, states[name], fetch_html, seen_store, notifier, tunnel_instructions)
            succeeded += 1
        except Exception as exc:
            logger.error("error polling '%s': %s", name, exc)
    return succeeded


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
    consecutive_failed_rounds = 0
    while True:
        succeeded = await run_one_round(
            searches, states, fetch_html, seen_store, notifier, tunnel_instructions
        )
        if succeeded > 0:
            consecutive_failed_rounds = 0
        else:
            consecutive_failed_rounds += 1
            if consecutive_failed_rounds >= MAX_CONSECUTIVE_ROUND_FAILURES:
                message = (
                    f"carouauto: all searches have failed for {consecutive_failed_rounds} "
                    "consecutive rounds — the browser may be dead. Restarting."
                )
                logger.error(message)
                try:
                    notifier.send_alert(message)
                except Exception as exc:  # never let the alert mask the restart
                    logger.error("failed to send browser-dead alert: %s", exc)
                raise BrowserLikelyDeadError(message)
        jitter = poll_interval_seconds * poll_jitter_fraction
        await asyncio.sleep(poll_interval_seconds + random.uniform(-jitter, jitter))
