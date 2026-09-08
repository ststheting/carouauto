from __future__ import annotations

import re

from .models import Listing

_PRICE_RE = re.compile(r"[\d,]+(?:\.\d+)?")


def parse_price(price_text: str) -> float | None:
    match = _PRICE_RE.search(price_text)
    if not match:
        return None
    return float(match.group(0).replace(",", ""))


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


def passes_filters(
    listing: Listing,
    min_price: float | None,
    max_price: float | None,
    exclude_keywords: str | None,
    condition_filter: str | None,
) -> bool:
    return (
        _matches_price(listing, min_price, max_price)
        and _matches_exclude_keywords(listing, exclude_keywords)
        and _matches_condition(listing, condition_filter)
    )
