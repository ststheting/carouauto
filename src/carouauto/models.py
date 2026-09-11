from dataclasses import dataclass


@dataclass(frozen=True)
class Listing:
    listing_id: str
    title: str
    price: str
    url: str
    thumbnail_url: str
    posted_text: str
    condition: str
    # Populated separately (from the listing's own page, not the search-results
    # page) by scheduler.run_cycle_for_url. None means not checked.
    is_bumped: bool | None = None
