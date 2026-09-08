import os
import sys

import httpx
from dotenv import load_dotenv


def main() -> None:
    load_dotenv()
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("Set TELEGRAM_BOT_TOKEN in .env first.")
        sys.exit(1)

    print("Send any message to your bot on Telegram now, then press Enter here.")
    input()

    response = httpx.get(f"https://api.telegram.org/bot{token}/getUpdates")
    response.raise_for_status()
    updates = response.json()["result"]
    if not updates:
        print("No messages found yet. Make sure you messaged the bot, then re-run this script.")
        sys.exit(1)

    chat_id = updates[-1]["message"]["chat"]["id"]
    print(f"Your chat ID is: {chat_id}")
    print("Add this to your .env as TELEGRAM_CHAT_ID=<that number>")


if __name__ == "__main__":
    main()
