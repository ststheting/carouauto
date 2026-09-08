_CHALLENGE_MARKERS = (
    "just a moment",
    "checking your browser",
    "cf-turnstile",
    "enable javascript and cookies to continue",
)


def is_challenge_page(html: str) -> bool:
    lowered = html.lower()
    return any(marker in lowered for marker in _CHALLENGE_MARKERS)
