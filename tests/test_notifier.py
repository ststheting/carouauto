import httpx
import pytest

from carouauto.models import Listing
from carouauto.notifier import MAX_MESSAGE_CHARS, TelegramNotifier, format_message

BOT_TOKEN = "123456:SUPER-SECRET-BOT-TOKEN"
CHAT_ID = 999


class FakeClient:
    """Stands in for httpx.Client, recording posts and optionally failing."""

    def __init__(self, fail_times=0):
        self.posts = []
        self._fail_times = fail_times

    def post(self, url, data=None, files=None):
        self.posts.append((url, data, files))
        if len(self.posts) <= self._fail_times:
            raise httpx.ConnectError(f"connection failed for {url}")
        return httpx.Response(200, request=httpx.Request("POST", url))


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    monkeypatch.setattr("carouauto.notifier.time.sleep", lambda _: None)


def make_listing(i, title_len=0):
    return Listing(
        listing_id=str(i),
        title=f"Item {i}" + "x" * title_len,
        price="S$10",
        url=f"https://www.carousell.sg/p/item-{i}/",
        thumbnail_url="",
        posted_text="1 hour ago",
        condition="Well used",
    )


def test_format_message_includes_title_price_time_and_url():
    listing = Listing(
        listing_id="1460198499",
        title="Speediance Gym Monster Smart Home Fitness Machine",
        price="S$599",
        url="https://www.carousell.sg/p/speediance-gym-monster-1460198499/",
        thumbnail_url="https://media.karousell.com/thumb.jpg",
        posted_text="18 hours ago",
        condition="Well used",
    )

    message = format_message(listing)

    assert "Speediance Gym Monster Smart Home Fitness Machine" in message
    assert "S$599" in message
    assert "18 hours ago" in message
    assert "https://www.carousell.sg/p/speediance-gym-monster-1460198499/" in message


def test_format_message_labels_stale_posted_text_as_possibly_bumped():
    listing = Listing(
        listing_id="1", title="Item", price="S$10",
        url="https://www.carousell.sg/p/item-1/", thumbnail_url="",
        posted_text="4 hours ago", condition="Well used",
    )

    message = format_message(listing)

    assert message.startswith("🔁 Possibly re-surfaced/bumped")


def test_format_message_does_not_label_fresh_posted_text():
    listing = Listing(
        listing_id="1", title="Item", price="S$10",
        url="https://www.carousell.sg/p/item-1/", thumbnail_url="",
        posted_text="4 minutes ago", condition="Well used",
    )

    message = format_message(listing)

    assert "🔁" not in message
    assert message.startswith("Item")


def test_format_message_omits_blank_price_and_posted_text():
    listing = Listing(
        listing_id="1", title="Item", price="", url="https://www.carousell.sg/p/item-1/",
        thumbnail_url="", posted_text="", condition="",
    )

    message = format_message(listing)

    assert message == "Item\nhttps://www.carousell.sg/p/item-1/"


def test_send_retries_once_then_succeeds():
    client = FakeClient(fail_times=1)
    notifier = TelegramNotifier(BOT_TOKEN, client=client)

    notifier.send_alert(CHAT_ID, "hello")

    assert len(client.posts) == 2


def test_send_raises_after_one_retry_without_leaking_the_token():
    client = FakeClient(fail_times=99)
    notifier = TelegramNotifier(BOT_TOKEN, client=client)

    with pytest.raises(RuntimeError) as excinfo:
        notifier.send_alert(CHAT_ID, "hello")

    assert len(client.posts) == 2
    assert BOT_TOKEN not in str(excinfo.value)
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__context__ is None


def test_send_text_goes_to_the_given_chat_id():
    client = FakeClient()
    notifier = TelegramNotifier(BOT_TOKEN, client=client)

    notifier.send_text(555, "a reply")

    _, data, _ = client.posts[0]
    assert data["chat_id"] == 555
    assert data["text"] == "a reply"


def test_large_batch_is_split_into_messages_under_the_size_limit():
    client = FakeClient()
    notifier = TelegramNotifier(BOT_TOKEN, client=client)
    listings = [make_listing(i, title_len=200) for i in range(60)]

    notifier.send_new_listings(CHAT_ID, "speediance", listings)

    assert len(client.posts) > 1
    for _, data, _ in client.posts:
        assert len(data["text"]) <= MAX_MESSAGE_CHARS
    assert client.posts[0][1]["text"].startswith("60 new listings for 'speediance':")
    for _, data, _ in client.posts[1:]:
        assert "new listings for" not in data["text"]
    combined = "".join(data["text"] for _, data, _ in client.posts)
    for listing in listings:
        assert listing.url in combined


def test_small_batch_still_sends_a_single_message():
    client = FakeClient()
    notifier = TelegramNotifier(BOT_TOKEN, client=client)

    notifier.send_new_listings(CHAT_ID, "speediance", [make_listing(1), make_listing(2)])

    assert len(client.posts) == 1
    assert client.posts[0][1]["text"].startswith("2 new listings for 'speediance':")


def test_send_document_posts_the_file_with_the_given_chat_id(tmp_path):
    client = FakeClient()
    notifier = TelegramNotifier(BOT_TOKEN, client=client)
    file_path = tmp_path / "backup.sqlite3"
    file_path.write_bytes(b"fake sqlite bytes")

    notifier.send_document(777, str(file_path), "carouauto_backup.sqlite3")

    url, data, files = client.posts[0]
    assert url.endswith("/sendDocument")
    assert data["chat_id"] == 777
    assert "document" in files


def test_send_document_raises_without_leaking_the_token(tmp_path):
    client = FakeClient(fail_times=99)
    notifier = TelegramNotifier(BOT_TOKEN, client=client)
    file_path = tmp_path / "backup.sqlite3"
    file_path.write_bytes(b"fake sqlite bytes")

    with pytest.raises(RuntimeError) as excinfo:
        notifier.send_document(777, str(file_path), "carouauto_backup.sqlite3")

    assert len(client.posts) == 1
    assert BOT_TOKEN not in str(excinfo.value)
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__context__ is None
