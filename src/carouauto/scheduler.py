from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Awaitable, Callable

from .bump import is_bumped_listing
from .challenge import is_challenge_page
from .db import SeenStore
from .filters import passes_filters
from .parser import parse_listings
from .subscriptions import UserSearch

FetchHtmlFn = Callable[[str], Awaitable[str]]

CHALLENGE_REMINDER_AFTER_SECONDS = 30 * 60
MAX_CONSECUTIVE_ROUND_FAILURES = 5

# The Playwright driver's own memory grows over many hours of continuous page
# churn and has been observed to hit its heap limit roughly once a day on
# this VPS's tight RAM budget. Recycling well before that window is up trades
# one uncontrolled ~20-30 minute outage (the time to detect and react to an
# actual crash) for a clean, sub-minute restart on our own schedule.
BROWSER_MAX_AGE_SECONDS = 4 * 60 * 60

logger = logging.getLogger("carouauto")


class BrowserLikelyDeadError(RuntimeError):
    """Raised to exit the process so systemd restarts it with a fresh browser."""


class ScheduledBrowserRecycle(RuntimeError):
    """Raised to proactively exit the process on a timer, before hours of
    continuous Playwright page churn risk the driver's own memory limit —
    routine maintenance, not a failure, so this doesn't alert the admin."""


@dataclass
class SearchState:
    paused: bool = False
    paused_since: datetime | None = None
    reminder_sent: bool = False
    last_polled_at: datetime | None = None


async def run_cycle_for_url(
    url: str,
    subscribers: list[UserSearch],
    state: SearchState,
    fetch_html: FetchHtmlFn,
    seen_store: SeenStore,
    notifier,
    tunnel_instructions: str,
    admin_chat_id: int,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> None:
    html = await fetch_html(url)

    if is_challenge_page(html):
        if not state.paused:
            state.paused = True
            state.paused_since = now()
            state.reminder_sent = False
            notifier.send_alert(
                admin_chat_id,
                f"Cloudflare challenge is blocking '{url}'. Solve it manually:\n{tunnel_instructions}",
            )
        elif not state.reminder_sent and state.paused_since is not None:
            elapsed = (now() - state.paused_since).total_seconds()
            if elapsed > CHALLENGE_REMINDER_AFTER_SECONDS:
                state.reminder_sent = True
                notifier.send_alert(
                    admin_chat_id, f"Still waiting on a manual challenge solve for '{url}'."
                )
        logger.info("url '%s': challenge detected, paused", url)
        return

    if state.paused:
        state.paused = False
        state.paused_since = None
        state.reminder_sent = False

    listings = parse_listings(html)
    all_ids = [l.listing_id for l in listings]

    state.last_polled_at = now()

    new_ids_by_search: dict[int, list[str]] = {
        sub.search_id: seen_store.get_new_ids(sub.search_id, all_ids) for sub in subscribers
    }
    candidate_ids = {lid for ids in new_ids_by_search.values() for lid in ids}
    bump_status: dict[str, bool] = {}
    for listing in listings:
        if listing.listing_id not in candidate_ids:
            continue
        try:
            detail_html = await fetch_html(listing.url)
        except Exception as exc:
            # A single listing's detail page failing to load must not stop
            # the rest of this cycle — leave it unchecked (fail-open, still
            # shown, no bump badge) rather than lose the whole poll.
            logger.error("bump check failed for '%s': %s", listing.url, type(exc).__name__)
            continue
        is_bumped = is_bumped_listing(detail_html)
        if is_bumped is not None:
            bump_status[listing.listing_id] = is_bumped
    listings = [
        replace(l, is_bumped=bump_status[l.listing_id]) if l.listing_id in bump_status else l
        for l in listings
    ]

    for sub in subscribers:
        new_ids = new_ids_by_search[sub.search_id]
        new_listings = [l for l in listings if l.listing_id in new_ids]
        filtered = [
            l
            for l in new_listings
            if passes_filters(
                l, sub.min_price, sub.max_price, sub.exclude_keywords, sub.condition_filter, sub.hide_bumped
            )
        ]
        try:
            if filtered:
                # If this raises, mark_seen below is skipped, so the same ids
                # are retried for this subscriber next cycle.
                notifier.send_new_listings(sub.chat_id, sub.name, filtered)
            seen_store.mark_seen(sub.search_id, all_ids)
        except Exception as exc:
            # A single subscriber's notify failure (e.g. a blocked/deactivated
            # Telegram user) must not stop every other subscriber of this URL
            # from being processed.
            logger.error("notify failed for search_id=%s: %s", sub.search_id, exc)

    logger.info("url '%s': polled, %d subscriber(s)", url, len(subscribers))


async def run_one_round(
    subscriptions: list[UserSearch],
    states: dict[str, SearchState],
    fetch_html: FetchHtmlFn,
    seen_store: SeenStore,
    notifier,
    tunnel_instructions: str,
    admin_chat_id: int,
) -> tuple[int, int]:
    """Poll every distinct URL once, fanning out to its subscribers.

    Returns (succeeded, total) URL counts. One URL failing must never
    stop the others from being polled.
    """
    by_url: dict[str, list[UserSearch]] = {}
    for sub in subscriptions:
        by_url.setdefault(sub.url, []).append(sub)

    succeeded = 0
    for url, subs in by_url.items():
        states.setdefault(url, SearchState())
        try:
            await run_cycle_for_url(
                url, subs, states[url], fetch_html, seen_store, notifier, tunnel_instructions, admin_chat_id
            )
            succeeded += 1
        except Exception as exc:
            logger.error("error polling '%s': %s", url, exc)
    return succeeded, len(by_url)


async def run_forever(
    get_subscriptions: Callable[[], list[UserSearch]],
    states: dict[str, SearchState],
    fetch_html: FetchHtmlFn,
    seen_store: SeenStore,
    notifier,
    tunnel_instructions: str,
    admin_chat_id: int,
    poll_interval_seconds: float,
    poll_jitter_fraction: float,
    browser_started_at: datetime | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> None:
    browser_started_at = browser_started_at if browser_started_at is not None else now()
    consecutive_failed_rounds = 0
    while True:
        subscriptions = get_subscriptions()
        succeeded, total = await run_one_round(
            subscriptions, states, fetch_html, seen_store, notifier, tunnel_instructions, admin_chat_id
        )
        if total == 0 or succeeded > 0:
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
                    notifier.send_alert(admin_chat_id, message)
                except Exception as exc:
                    logger.error("failed to send browser-dead alert: %s", exc)
                raise BrowserLikelyDeadError(message)

        if (now() - browser_started_at).total_seconds() > BROWSER_MAX_AGE_SECONDS:
            message = (
                f"carouauto: recycling the browser after {BROWSER_MAX_AGE_SECONDS // 3600}h "
                "to avoid long-run memory buildup."
            )
            logger.info(message)
            raise ScheduledBrowserRecycle(message)

        jitter = poll_interval_seconds * poll_jitter_fraction
        await asyncio.sleep(poll_interval_seconds + random.uniform(-jitter, jitter))
