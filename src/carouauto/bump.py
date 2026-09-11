import re

from bs4 import BeautifulSoup

# A listing's own page shows a details-grid row labelled "Listed" for a
# never-bumped item, or "Bumped" for one that was recently (paid- or
# free-) bumped — Carousell swaps the label itself, so this is a direct
# signal rather than a guess from timestamp text.
_LABELS = {"Listed", "Bumped"}

# The bot exists to surface genuinely new listings, so a "Listed" item this
# old is just as uninteresting as a resurfaced "Bumped" one — both count as
# "bumped" for filtering purposes. Not user-configurable; change this
# constant if the window ever needs to move.
FRESH_WINDOW_DAYS = 1

_AGE_RE = re.compile(r"(\d+)\s+(minute|hour|day|week|month|year)s?\s+ago", re.IGNORECASE)
_DAYS_PER_UNIT = {
    "minute": 1 / 1440,
    "hour": 1 / 24,
    "day": 1,
    "week": 7,
    "month": 30,
    "year": 365,
}


def parse_age_days(age_text: str) -> float | None:
    text = age_text.strip()
    if not text:
        return None
    lowered = text.lower()
    if lowered in ("just now", "moments ago"):
        return 0.0
    if lowered == "yesterday":
        return 1.0
    if "over a year" in lowered:
        return 366.0
    match = _AGE_RE.search(text)
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2).lower()
    return amount * _DAYS_PER_UNIT[unit]


def is_bumped_listing(html: str) -> bool | None:
    soup = BeautifulSoup(html, "lxml")
    for label_p in soup.find_all("p"):
        text = label_p.get_text(strip=True)
        if text not in _LABELS:
            continue
        if text == "Bumped":
            return True
        # text == "Listed" — a paid/free bump isn't the only way a listing
        # can be stale: one that's simply old and never bumped is just as
        # uninteresting to a "new listings" feed.
        value_el = label_p.find_next_sibling()
        value_text = value_el.get_text(" ", strip=True) if value_el else ""
        age_days = parse_age_days(value_text)
        return age_days is not None and age_days > FRESH_WINDOW_DAYS
    return None
