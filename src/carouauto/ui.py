from __future__ import annotations

MAX_CALLBACK_DATA_BYTES = 64


def inline_keyboard(rows: list[list[tuple[str, str]]]) -> dict:
    for row in rows:
        for text, callback_data in row:
            if len(callback_data.encode()) > MAX_CALLBACK_DATA_BYTES:
                raise ValueError(
                    f"callback_data '{callback_data}' exceeds Telegram's {MAX_CALLBACK_DATA_BYTES}-byte limit"
                )
    return {
        "inline_keyboard": [
            [{"text": text, "callback_data": data} for text, data in row] for row in rows
        ]
    }
