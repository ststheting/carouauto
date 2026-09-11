from bs4 import BeautifulSoup

# A listing's own page shows a details-grid row labelled "Listed" for a
# never-bumped item, or "Bumped" for one that was recently (paid- or
# free-) bumped — Carousell swaps the label itself, so this is a direct
# signal rather than a guess from timestamp text.
_LABELS = {"Listed", "Bumped"}


def is_bumped_listing(html: str) -> bool | None:
    soup = BeautifulSoup(html, "lxml")
    for p in soup.find_all("p"):
        text = p.get_text(strip=True)
        if text in _LABELS:
            return text == "Bumped"
    return None
