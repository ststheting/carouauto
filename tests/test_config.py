import pytest
from carouauto.config import load_config, SearchConfig


@pytest.fixture(autouse=True)
def clean_telegram_env(monkeypatch):
    """load_config now consults os.environ first, so tests must not inherit it."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)


def test_load_config_reads_searches_and_env(tmp_path):
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text(
        "poll_interval_seconds: 60\n"
        "poll_jitter_fraction: 0.1\n"
        "searches:\n"
        "  - name: speediance\n"
        "    url: https://www.carousell.sg/search/speediance?sort_by=3\n"
    )
    env_file = tmp_path / ".env"
    env_file.write_text("TELEGRAM_BOT_TOKEN=abc123\nTELEGRAM_CHAT_ID=999\n")

    config = load_config(str(config_yaml), str(env_file))

    assert config.poll_interval_seconds == 60
    assert config.poll_jitter_fraction == 0.1
    assert config.searches == [
        SearchConfig(name="speediance", url="https://www.carousell.sg/search/speediance?sort_by=3")
    ]
    assert config.telegram_bot_token == "abc123"
    assert config.telegram_chat_id == "999"


def test_load_config_raises_when_no_searches(tmp_path):
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text("searches: []\n")
    env_file = tmp_path / ".env"
    env_file.write_text("TELEGRAM_BOT_TOKEN=abc123\nTELEGRAM_CHAT_ID=999\n")

    with pytest.raises(ValueError, match="at least one search"):
        load_config(str(config_yaml), str(env_file))


def test_load_config_raises_when_missing_telegram_env(tmp_path):
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text("searches:\n  - name: speediance\n    url: https://example.com\n")
    env_file = tmp_path / ".env"
    env_file.write_text("")

    with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN"):
        load_config(str(config_yaml), str(env_file))


def test_process_environment_takes_precedence_over_env_file(tmp_path, monkeypatch):
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text("searches:\n  - name: speediance\n    url: https://example.com\n")
    env_file = tmp_path / ".env"
    env_file.write_text("TELEGRAM_BOT_TOKEN=from-file\nTELEGRAM_CHAT_ID=from-file\n")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "from-process")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "from-process")

    config = load_config(str(config_yaml), str(env_file))

    assert config.telegram_bot_token == "from-process"
    assert config.telegram_chat_id == "from-process"


def test_process_environment_alone_is_sufficient(tmp_path, monkeypatch):
    """systemd's EnvironmentFile= populates the process env; no .env file need exist."""
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text("searches:\n  - name: speediance\n    url: https://example.com\n")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "abc123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "999")

    config = load_config(str(config_yaml), str(tmp_path / "does-not-exist.env"))

    assert config.telegram_bot_token == "abc123"
    assert config.telegram_chat_id == "999"
