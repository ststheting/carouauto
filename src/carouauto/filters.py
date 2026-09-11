from __future__ import annotations

import re

from .models import Listing

_PRICE_RE = re.compile(r"[\d,]+(?:\.\d+)?")
_AGE_RE = re.compile(r"(\d+)\s+(minute|hour|day|week|month|year)s?\s+ago", re.IGNORECASE)
_DAYS_PER_UNIT = {
    "minute": 1 / 1440,
    "hour": 1 / 24,
    "day": 1,
    "week": 7,
    "month": 30,
    "year": 365,
}


def parse_price(price_text: str) -> float | None:
    match = _PRICE_RE.search(price_text)
    if not match:
        return None
    return float(match.group(0).replace(",", ""))


def parse_age_days(posted_text: str) -> float | None:
    text = posted_text.strip()
    if not text:
        return None
    lowered = text.lower()
    if lowered == "just now":
        return 0.0
    if lowered == "yesterday":
        return 1.0
    match = _AGE_RE.search(text)
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2).lower()
    return amount * _DAYS_PER_UNIT[unit]


def _matches_price(listing: Listing, min_price: float | None, max_price: float | None) -> bool:
    if min_price is None and max_price is None:
        return True
    price = parse_price(listing.price)
    if price is None:
        return True  # can't parse it — don't filter out on an unknown price
    if min_price is not None and price < min_price:
        return False
    if max_price is not None and price > max_price:
        return False
    return True


def _matches_exclude_keywords(listing: Listing, exclude_keywords: str | None) -> bool:
    if not exclude_keywords:
        return True
    title_lower = listing.title.lower()
    for word in exclude_keywords.split(","):
        word = word.strip().lower()
        if word and word in title_lower:
            return False
    return True


def _matches_condition(listing: Listing, condition_filter: str | None) -> bool:
    if not condition_filter:
        return True
    return listing.condition.strip().lower() == condition_filter.strip().lower()


def _matches_bump_filter(listing: Listing, hide_bumped: bool) -> bool:
    if not hide_bumped:
        return True
    return listing.is_bumped is not True


def _matches_max_age(listing: Listing, max_age_days: float | None) -> bool:
    if max_age_days is None:
        return True
    age = parse_age_days(listing.posted_text)
    if age is None:
        return True  # can't parse it — don't filter out on an unknown age
    return age <= max_age_days


def passes_filters(
    listing: Listing,
    min_price: float | None,
    max_price: float | None,
    exclude_keywords: str | None,
    condition_filter: str | None,
    hide_bumped: bool = False,
    max_age_days: float | None = None,
) -> bool:
    return (
        _matches_price(listing, min_price, max_price)
        and _matches_exclude_keywords(listing, exclude_keywords)
        and _matches_condition(listing, condition_filter)
        and _matches_bump_filter(listing, hide_bumped)
        and _matches_max_age(listing, max_age_days)
    )
