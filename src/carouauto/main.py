from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from .commands import BotContext
from .config import load_config
from .db import SeenStore
from .notifier import TelegramNotifier
from .poller import Poller
from .scheduler import SearchState, run_forever
from .subscriptions import SubscriptionStore
from .telegram_listener import run_command_listener

TUNNEL_INSTRUCTIONS = (
    "1. From your machine: ssh -L 6080:localhost:6080 <user>@<vps-ip>\n"
    "2. Open http://localhost:6080/vnc.html in your browser\n"
    "3. Solve the challenge in the browser window shown\n"
)


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


async def main() -> None:
    configure_logging()
    config = load_config()
    admin_chat_id = int(config.telegram_chat_id)

    seen_store = SeenStore(config.db_path)
    subscriptions = SubscriptionStore(config.db_path)
    subscriptions.seed_admin(admin_chat_id)
    notifier = TelegramNotifier(config.telegram_bot_token)
    poller = Poller(user_data_dir=config.user_data_dir, headless=False)
    states: dict[str, SearchState] = {}

    ctx = BotContext(
        subscriptions=subscriptions,
        seen_store=seen_store,
        notifier=notifier,
        registration_password=config.registration_password,
        fetch_html=poller.fetch_search_html,
        states=states,
        db_path=config.db_path,
        poll_interval_seconds=config.poll_interval_seconds,
    )

    await poller.start()
    browser_started_at = datetime.now(timezone.utc)
    try:
        await asyncio.gather(
            run_forever(
                get_subscriptions=subscriptions.list_active_subscriptions,
                states=states,
                fetch_html=poller.fetch_search_html,
                seen_store=seen_store,
                notifier=notifier,
                tunnel_instructions=TUNNEL_INSTRUCTIONS,
                admin_chat_id=admin_chat_id,
                poll_interval_seconds=config.poll_interval_seconds,
                poll_jitter_fraction=config.poll_jitter_fraction,
                browser_started_at=browser_started_at,
            ),
            run_command_listener(config.telegram_bot_token, ctx, admin_chat_id),
        )
    finally:
        try:
            await poller.stop()
        except Exception as exc:
            # If the browser/driver already died (the usual reason we're
            # exiting in the first place), closing it can itself raise —
            # that must not replace/mask the real exception this finally
            # block is unwinding for.
            logging.getLogger("carouauto").error("error stopping poller during shutdown: %s", type(exc).__name__)
        seen_store.close()
        subscriptions.close()


if __name__ == "__main__":
    asyncio.run(main())
