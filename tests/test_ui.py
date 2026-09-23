from carouauto import ui
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


def test_search_panel_keyboard_toggle_labels_reflect_current_state():
    sub = make_sub(search_id=7, paused=False, hide_bumped=True)

    markup = ui.search_panel_keyboard(sub)

    flat = [btn for row in markup["inline_keyboard"] for btn in row]
    by_data = {btn["callback_data"]: btn["text"] for btn in flat}
    assert by_data["tg:7:pa"] == "⏸ Pause"
    assert by_data["tg:7:hb"] == "👁 Show bumped"
    assert "ls" in by_data


def test_condition_keyboard_has_one_button_per_condition_plus_any():
    sub = make_sub(search_id=3)

    markup = ui.condition_keyboard(sub)

    flat = [b for row in markup["inline_keyboard"] for b in row]
    assert [b["text"] for b in flat[:-2]] == list(ui.CONDITIONS)
    assert flat[-2]["text"] == "Any"
    assert flat[-2]["callback_data"] == "cds:3:5"
    assert flat[-1]["callback_data"] == "sp:3"  # back to panel
