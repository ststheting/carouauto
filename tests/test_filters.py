import pytest

from carouauto.filters import parse_age_days, parse_price, passes_filters
from carouauto.models import Listing


def make_listing(price="S$100", title="Item", condition="Well used", is_bumped=None,
                  posted_text="1 minute ago"):
    return Listing(
        listing_id="1",
        title=title,
        price=price,
        url="https://example.com/1",
        thumbnail_url="",
        posted_text=posted_text,
        condition=condition,
        is_bumped=is_bumped,
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


def test_hide_bumped_off_by_default():
    listing = make_listing(is_bumped=True)
    assert passes_filters(listing, None, None, None, None) is True


def test_hide_bumped_excludes_a_confirmed_bumped_listing():
    listing = make_listing(is_bumped=True)
    assert passes_filters(listing, None, None, None, None, hide_bumped=True) is False


def test_hide_bumped_keeps_a_confirmed_not_bumped_listing():
    listing = make_listing(is_bumped=False)
    assert passes_filters(listing, None, None, None, None, hide_bumped=True) is True


def test_hide_bumped_keeps_an_unchecked_listing():
    listing = make_listing(is_bumped=None)
    assert passes_filters(listing, None, None, None, None, hide_bumped=True) is True


def test_parse_age_days_handles_just_now_and_yesterday():
    assert parse_age_days("Just now") == 0.0
    assert parse_age_days("just now") == 0.0
    assert parse_age_days("Yesterday") == 1.0


def test_parse_age_days_handles_units():
    assert parse_age_days("30 minutes ago") == pytest.approx(30 / 1440)
    assert parse_age_days("2 hours ago") == pytest.approx(2 / 24)
    assert parse_age_days("3 days ago") == 3
    assert parse_age_days("2 weeks ago") == 14
    assert parse_age_days("6 months ago") == 180
    assert parse_age_days("8 years ago") == 8 * 365


def test_parse_age_days_returns_none_for_unparseable_text():
    assert parse_age_days("") is None
    assert parse_age_days("over a year ago") is None


def test_max_age_off_by_default():
    listing = make_listing(posted_text="8 years ago")
    assert passes_filters(listing, None, None, None, None) is True


def test_max_age_excludes_a_listing_older_than_the_limit():
    listing = make_listing(posted_text="8 years ago")
    assert passes_filters(listing, None, None, None, None, max_age_days=90) is False


def test_max_age_keeps_a_listing_within_the_limit():
    listing = make_listing(posted_text="3 days ago")
    assert passes_filters(listing, None, None, None, None, max_age_days=90) is True


def test_max_age_does_not_exclude_unparseable_posted_text():
    listing = make_listing(posted_text="")
    assert passes_filters(listing, None, None, None, None, max_age_days=90) is True
