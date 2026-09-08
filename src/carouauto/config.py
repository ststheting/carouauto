from __future__ import annotations

import os
from dataclasses import dataclass

import yaml
from dotenv import dotenv_values


@dataclass
class AppConfig:
    poll_interval_seconds: float
    poll_jitter_fraction: float
    telegram_bot_token: str
    telegram_chat_id: str
    registration_password: str
    db_path: str
    user_data_dir: str


def load_config(config_path: str = "config.yaml", env_path: str = ".env") -> AppConfig:
    env_from_file = dotenv_values(env_path)

    with open(config_path) as f:
        raw = yaml.safe_load(f) or {}

    token = os.environ.get("TELEGRAM_BOT_TOKEN") or env_from_file.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID") or env_from_file.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in the environment")

    registration_password = os.environ.get("REGISTRATION_PASSWORD") or env_from_file.get(
        "REGISTRATION_PASSWORD"
    )
    if not registration_password:
        raise ValueError("REGISTRATION_PASSWORD must be set in the environment")

    return AppConfig(
        poll_interval_seconds=float(raw.get("poll_interval_seconds", 300)),
        poll_jitter_fraction=float(raw.get("poll_jitter_fraction", 0.2)),
        telegram_bot_token=token,
        telegram_chat_id=chat_id,
        registration_password=registration_password,
        db_path=raw.get("db_path", "carouauto.sqlite3"),
        user_data_dir=raw.get("user_data_dir", "browser-profile"),
    )
