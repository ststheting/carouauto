from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .models import Listing

BASE_URL = "https://www.carousell.sg"
_ID_RE = re.compile(r"listing-card-(\d+)")
_TIME_RE = re.compile(r"\d+\s+\w+\s+ago|Just now|Yesterday", re.IGNORECASE)


def parse_listings(html: str) -> list[Listing]:
    soup = BeautifulSoup(html, "lxml")
    listings: list[Listing] = []

    for card in soup.select('[data-testid^="listing-card-"]'):
        match = _ID_RE.search(card.get("data-testid", ""))
        if not match:
            continue
        listing_id = match.group(1)

        listing_link = card.select_one('a[href^="/p/"]')
        if listing_link is None:
            continue

        img = listing_link.select_one("img")
        title = img.get("alt", "").strip() if img else ""
        thumbnail_url = img.get("src", "") if img else ""
        url = urljoin(BASE_URL, listing_link["href"].split("?")[0])

        price_el = listing_link.select_one('p[title^="S$"]')
        price = price_el["title"] if price_el else ""

        posted_text = ""
        seller_link = card.select_one('a[href^="/u/"]')
        if seller_link is not None:
            time_match = _TIME_RE.search(seller_link.get_text(" ", strip=True))
            posted_text = time_match.group(0) if time_match else ""

        listings.append(
            Listing(
                listing_id=listing_id,
                title=title,
                price=price,
                url=url,
                thumbnail_url=thumbnail_url,
                posted_text=posted_text,
            )
        )

    return listings
