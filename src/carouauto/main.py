from __future__ import annotations

import asyncio
import logging

from .config import load_config
from .db import SeenStore
from .notifier import TelegramNotifier
from .poller import Poller
from .scheduler import run_forever

TUNNEL_INSTRUCTIONS = (
    "1. From your machine: ssh -L 6080:localhost:6080 <user>@<vps-ip>\n"
    "2. Open http://localhost:6080/vnc.html in your browser\n"
    "3. Solve the challenge in the browser window shown\n"
)


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # httpx logs every request's full URL at INFO by default, which would
    # embed the Telegram bot token (it's part of the URL path) straight into
    # the journal. Our own request-level error handling already redacts it
    # (see notifier.py); this closes the same leak at the library's own
    # logging layer.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


async def main() -> None:
    configure_logging()
    config = load_config()
    seen_store = SeenStore(config.db_path)
    notifier = TelegramNotifier(config.telegram_bot_token, config.telegram_chat_id)
    poller = Poller(user_data_dir=config.user_data_dir, headless=False)

    await poller.start()
    try:
        await run_forever(
            searches=[(s.name, s.url) for s in config.searches],
            fetch_html=poller.fetch_search_html,
            seen_store=seen_store,
            notifier=notifier,
            tunnel_instructions=TUNNEL_INSTRUCTIONS,
            poll_interval_seconds=config.poll_interval_seconds,
            poll_jitter_fraction=config.poll_jitter_fraction,
        )
    finally:
        await poller.stop()
        seen_store.close()


if __name__ == "__main__":
    asyncio.run(main())
