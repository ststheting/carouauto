from dataclasses import dataclass


@dataclass(frozen=True)
class Listing:
    listing_id: str
    title: str
    price: str
    url: str
    thumbnail_url: str
    posted_text: str
