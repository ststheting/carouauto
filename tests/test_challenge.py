from pathlib import Path

from carouauto.challenge import is_challenge_page

FIXTURES = Path(__file__).parent / "fixtures"


def test_detects_cloudflare_interstitial_title():
    html = "<html><head><title>Just a moment...</title></head><body></body></html>"
    assert is_challenge_page(html) is True


def test_detects_turnstile_widget():
    html = '<html><body><div class="cf-turnstile" data-sitekey="x"></div></body></html>'
    assert is_challenge_page(html) is True


def test_normal_search_results_page_is_not_a_challenge():
    html = (FIXTURES / "search_results_normal.html").read_text()
    assert is_challenge_page(html) is False


def test_empty_page_is_not_a_challenge():
    assert is_challenge_page("") is False
