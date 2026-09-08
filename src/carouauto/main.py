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


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
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
