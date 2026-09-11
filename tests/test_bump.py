from carouauto.bump import is_bumped_listing

LISTED_HTML = """
<html><body>
<div><p>Condition</p><p>Lightly used</p></div>
<div><p>Type</p><p>Chairs &amp; Arm Chairs</p></div>
<div><p>Listed</p><div><p>over a year ago by <a href="/u/seller/">seller</a></p></div></div>
</body></html>
"""

BUMPED_HTML = """
<html><body>
<div><p>Condition</p><p>Lightly used</p></div>
<div><p>Bumped</p><div><p>moments ago by <a href="/u/seller/">seller</a></p></div></div>
</body></html>
"""


def test_detects_a_bumped_listing():
    assert is_bumped_listing(BUMPED_HTML) is True


def test_detects_a_never_bumped_listing():
    assert is_bumped_listing(LISTED_HTML) is False


def test_returns_none_when_neither_label_is_present():
    assert is_bumped_listing("<html><body><p>Something else</p></body></html>") is None


def test_returns_none_for_empty_html():
    assert is_bumped_listing("") is None
