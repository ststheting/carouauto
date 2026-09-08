from carouauto.filters import parse_price, passes_filters
from carouauto.models import Listing


def make_listing(price="S$100", title="Item", condition="Well used"):
    return Listing(
        listing_id="1",
        title=title,
        price=price,
        url="https://example.com/1",
        thumbnail_url="",
        posted_text="1 minute ago",
        condition=condition,
    )


def test_parse_price_extracts_the_number():
    assert parse_price("S$599") == 599.0
    assert parse_price("S$1,250.50") == 1250.50


def test_parse_price_returns_none_for_unparseable_text():
    assert parse_price("") is None
    assert parse_price("Free") is None


def test_passes_filters_with_no_filters_set_always_true():
    listing = make_listing()
    assert passes_filters(listing, None, None, None, None) is True


def test_price_filter_excludes_outside_range():
    listing = make_listing(price="S$50")
    assert passes_filters(listing, 100.0, 500.0, None, None) is False
    assert passes_filters(listing, 10.0, 100.0, None, None) is True


def test_price_filter_does_not_exclude_unparseable_price():
    listing = make_listing(price="Contact for price")
    assert passes_filters(listing, 100.0, 500.0, None, None) is True


def test_exclude_keywords_matches_case_insensitively_in_title():
    listing = make_listing(title="Nintendo Switch CASE only, no console")
    assert passes_filters(listing, None, None, "case", None) is False
    assert passes_filters(listing, None, None, "box,manual", None) is True


def test_condition_filter_requires_exact_case_insensitive_match():
    listing = make_listing(condition="Brand new")
    assert passes_filters(listing, None, None, None, "brand new") is True
    assert passes_filters(listing, None, None, None, "Well used") is False


def test_all_filters_combine_with_and():
    listing = make_listing(price="S$50", title="Item with case", condition="Well used")
    assert passes_filters(listing, 10.0, 100.0, "case", "Well used") is False
    assert passes_filters(listing, 10.0, 100.0, "box", "Well used") is True
