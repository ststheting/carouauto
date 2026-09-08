from carouauto.models import Listing
from carouauto.notifier import format_message


def test_format_message_includes_title_price_time_and_url():
    listing = Listing(
        listing_id="1460198499",
        title="Speediance Gym Monster Smart Home Fitness Machine",
        price="S$599",
        url="https://www.carousell.sg/p/speediance-gym-monster-1460198499/",
        thumbnail_url="https://media.karousell.com/thumb.jpg",
        posted_text="18 hours ago",
    )

    message = format_message(listing)

    assert "Speediance Gym Monster Smart Home Fitness Machine" in message
    assert "S$599" in message
    assert "18 hours ago" in message
    assert "https://www.carousell.sg/p/speediance-gym-monster-1460198499/" in message


def test_format_message_omits_blank_price_and_posted_text():
    listing = Listing(
        listing_id="1",
        title="Item",
        price="",
        url="https://www.carousell.sg/p/item-1/",
        thumbnail_url="",
        posted_text="",
    )

    message = format_message(listing)

    assert message == "Item\nhttps://www.carousell.sg/p/item-1/"
