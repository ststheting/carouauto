import pytest

from carouauto.bump import FRESH_WINDOW_DAYS, is_bumped_listing, parse_age_days


def listed_html(age_text):
    return f"""
    <html><body>
    <div><p>Condition</p><p>Lightly used</p></div>
    <div><p>Listed</p><div><p>{age_text} by <a href="/u/seller/">seller</a></p></div></div>
    </body></html>
    """


BUMPED_HTML = """
<html><body>
<div><p>Condition</p><p>Lightly used</p></div>
<div><p>Bumped</p><div><p>moments ago by <a href="/u/seller/">seller</a></p></div></div>
</body></html>
"""


def test_a_bumped_listing_is_always_bumped_regardless_of_how_fresh_it_looks():
    assert is_bumped_listing(BUMPED_HTML) is True


def test_a_freshly_listed_never_bumped_listing_is_not_bumped():
    assert is_bumped_listing(listed_html("5 minutes ago")) is False


def test_a_listing_right_at_the_fresh_window_boundary_is_not_bumped():
    assert is_bumped_listing(listed_html("23 hours ago")) is False


def test_an_old_never_bumped_listing_counts_as_bumped_too():
    assert is_bumped_listing(listed_html("8 years ago")) is True
    assert is_bumped_listing(listed_html("over a year ago")) is True
    assert is_bumped_listing(listed_html("3 days ago")) is True


def test_a_listed_listing_with_unparseable_age_is_not_excluded():
    assert is_bumped_listing(listed_html("a while")) is False


def test_returns_none_when_neither_label_is_present():
    assert is_bumped_listing("<html><body><p>Something else</p></body></html>") is None


def test_returns_none_for_empty_html():
    assert is_bumped_listing("") is None


def test_parse_age_days_handles_just_now_moments_and_yesterday():
    assert parse_age_days("Just now") == 0.0
    assert parse_age_days("moments ago") == 0.0
    assert parse_age_days("Yesterday") == 1.0


def test_parse_age_days_handles_over_a_year():
    assert parse_age_days("over a year ago") == pytest.approx(366.0)


def test_parse_age_days_handles_units():
    assert parse_age_days("30 minutes ago") == pytest.approx(30 / 1440)
    assert parse_age_days("2 hours ago") == pytest.approx(2 / 24)
    assert parse_age_days("3 days ago") == 3
    assert parse_age_days("2 weeks ago") == 14
    assert parse_age_days("6 months ago") == 180
    assert parse_age_days("8 years ago") == 8 * 365


def test_parse_age_days_returns_none_for_unparseable_text():
    assert parse_age_days("") is None
    assert parse_age_days("a while") is None


def test_fresh_window_is_one_day():
    assert FRESH_WINDOW_DAYS == 1
