from datetime import datetime, timezone

from carouauto import ui
from carouauto.scheduler import SearchState
from carouauto.subscriptions import UserSearch


def test_inline_keyboard_builds_telegram_shaped_markup():
    markup = ui.inline_keyboard([[("Pause", "tg:1:pa"), ("Back", "ls")], [("Remove", "rm:1")]])

    assert markup == {
        "inline_keyboard": [
            [
                {"text": "Pause", "callback_data": "tg:1:pa"},
                {"text": "Back", "callback_data": "ls"},
            ],
            [{"text": "Remove", "callback_data": "rm:1"}],
        ]
    }


def test_inline_keyboard_rejects_callback_data_over_the_telegram_limit():
    too_long = "x" * (ui.MAX_CALLBACK_DATA_BYTES + 1)

    try:
        ui.inline_keyboard([[("Button", too_long)]])
        assert False, "expected a ValueError"
    except ValueError as exc:
        assert "64" in str(exc)


def make_sub(search_id=1, chat_id=111, name="speediance", paused=False, hide_bumped=True,
             min_price=None, max_price=None, exclude_keywords=None, condition_filter=None,
             url="https://example.com"):
    return UserSearch(
        search_id=search_id, chat_id=chat_id, name=name, url=url,
        min_price=min_price, max_price=max_price, exclude_keywords=exclude_keywords,
        condition_filter=condition_filter, paused=paused, hide_bumped=hide_bumped,
    )


def test_searches_list_keyboard_has_one_button_per_search():
    subs = [make_sub(search_id=1, name="speediance"), make_sub(search_id=2, name="switch")]

    markup = ui.searches_list_keyboard(subs)

    buttons = markup["inline_keyboard"]
    assert [row[0]["text"] for row in buttons] == ["speediance", "switch"]
    assert [row[0]["callback_data"] for row in buttons] == ["sp:1", "sp:2"]


def test_search_panel_text_reflects_state():
    sub = make_sub(name="speediance", paused=True, hide_bumped=False)

    text = ui.search_panel_text(sub)

    assert "speediance" in text
    assert "paused" in text.lower()


def test_search_panel_keyboard_includes_every_action():
    sub = make_sub(search_id=7, paused=False, hide_bumped=True)

    markup = ui.search_panel_keyboard(sub)

    flat = [b["callback_data"] for row in markup["inline_keyboard"] for b in row]
    assert "tg:7:pa" in flat
    assert "tg:7:hb" in flat
    assert "cd:7" in flat
    assert "pp:7" in flat
    assert "px:7" in flat
    assert "rm:7" in flat
    assert "ls" in flat


def test_condition_keyboard_has_one_button_per_condition_plus_any():
    sub = make_sub(search_id=3)

    markup = ui.condition_keyboard(sub)

    flat = [b for row in markup["inline_keyboard"] for b in row]
    assert [b["text"] for b in flat[:-2]] == list(ui.CONDITIONS)
    assert flat[-2]["text"] == "✓ Any"
    assert flat[-2]["callback_data"] == "cds:3:5"
    assert flat[-1]["callback_data"] == "sp:3"  # back to panel


def test_status_line_paused_by_user():
    assert ui.status_line(make_sub(paused=True), None) == "paused (by you)"


def test_status_line_cloudflare_pause():
    assert ui.status_line(make_sub(), SearchState(paused=True)) == (
        "paused (Cloudflare challenge, awaiting manual solve)"
    )


def test_status_line_last_checked_in_singapore_time():
    state = SearchState(last_polled_at=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc))
    assert ui.status_line(make_sub(), state) == "last checked 20:00 SGT"


def test_status_line_not_yet_checked():
    assert ui.status_line(make_sub(), None) == "not yet checked"
    assert ui.status_line(make_sub(), SearchState()) == "not yet checked"


def test_search_panel_text_includes_the_status_line():
    state = SearchState(last_polled_at=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc))
    text = ui.search_panel_text(make_sub(name="speediance"), state)
    assert "Status: last checked 20:00 SGT" in text


def test_search_panel_keyboard_has_a_current_listings_button():
    markup = ui.search_panel_keyboard(make_sub(search_id=7))
    flat = {b["callback_data"]: b["text"] for row in markup["inline_keyboard"] for b in row}
    assert flat["li:7"] == "👀 Current listings"


def test_condition_keyboard_marks_the_active_condition():
    markup = ui.condition_keyboard(make_sub(search_id=3, condition_filter="Like new"))
    texts = [b["text"] for row in markup["inline_keyboard"] for b in row]
    assert "✓ Like new" in texts
    assert "Brand new" in texts
    assert "Any" in texts


def test_remove_confirm_keyboard_has_yes_and_no():
    markup = ui.remove_confirm_keyboard(9)

    flat = [b for row in markup["inline_keyboard"] for b in row]
    by_data = {b["callback_data"]: b["text"] for b in flat}
    assert by_data["rmy:9"] == "Yes, remove it"
    assert by_data["rmn:9"] == "Cancel"
