from __future__ import annotations

import os
from dataclasses import dataclass

import yaml
from dotenv import dotenv_values


@dataclass
class SearchConfig:
    name: str
    url: str


@dataclass
class AppConfig:
    poll_interval_seconds: float
    poll_jitter_fraction: float
    searches: list[SearchConfig]
    telegram_bot_token: str
    telegram_chat_id: str
    db_path: str
    user_data_dir: str


def load_config(config_path: str = "config.yaml", env_path: str = ".env") -> AppConfig:
    # The process environment wins (systemd's EnvironmentFile= populates it
    # before we start); the .env file is a fallback for local development.
    env_from_file = dotenv_values(env_path)

    with open(config_path) as f:
        raw = yaml.safe_load(f) or {}

    searches = [SearchConfig(name=s["name"], url=s["url"]) for s in raw.get("searches") or []]
    if not searches:
        raise ValueError("config.yaml must define at least one search")

    token = os.environ.get("TELEGRAM_BOT_TOKEN") or env_from_file.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID") or env_from_file.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in the environment")

    return AppConfig(
        poll_interval_seconds=float(raw.get("poll_interval_seconds", 90)),
        poll_jitter_fraction=float(raw.get("poll_jitter_fraction", 0.2)),
        searches=searches,
        telegram_bot_token=token,
        telegram_chat_id=chat_id,
        db_path=raw.get("db_path", "carouauto.sqlite3"),
        user_data_dir=raw.get("user_data_dir", "browser-profile"),
    )
