from carouauto import ui


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
