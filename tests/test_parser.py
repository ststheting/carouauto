from pathlib import Path

from carouauto.models import Listing
from carouauto.parser import parse_listings

FIXTURES = Path(__file__).parent / "fixtures"


def test_parses_all_listing_cards():
    html = (FIXTURES / "search_results_normal.html").read_text()

    listings = parse_listings(html)

    assert len(listings) == 2


def test_extracts_correct_fields_for_a_listing():
    html = (FIXTURES / "search_results_normal.html").read_text()

    listings = parse_listings(html)
    speediance = next(l for l in listings if l.listing_id == "1460198499")

    assert speediance == Listing(
        listing_id="1460198499",
        title="Speediance Gym Monster Smart Home Fitness Machine",
        price="S$599",
        url="https://www.carousell.sg/p/speediance-gym-monster-smart-home-fitness-machine-1460198499/",
        thumbnail_url="https://media.karousell.com/media/photos/products/speediance_thumb.jpg",
        posted_text="18 hours ago",
        condition="Well used",
    )


def test_extracts_condition_for_second_listing():
    html = (FIXTURES / "search_results_normal.html").read_text()

    listings = parse_listings(html)
    switch = next(l for l in listings if l.listing_id == "1451610227")

    assert switch.condition == "Brand new"


def test_returns_empty_list_for_page_with_no_cards():
    assert parse_listings("<html><body>no results</body></html>") == []
