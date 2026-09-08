import pytest
from carouauto.config import load_config


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("REGISTRATION_PASSWORD", raising=False)


def write_env(tmp_path, token="abc123", chat_id="999", password="let-me-in"):
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"TELEGRAM_BOT_TOKEN={token}\nTELEGRAM_CHAT_ID={chat_id}\nREGISTRATION_PASSWORD={password}\n"
    )
    return env_file


def test_load_config_reads_settings_and_env(tmp_path):
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text("poll_interval_seconds: 60\npoll_jitter_fraction: 0.1\n")
    env_file = write_env(tmp_path)

    config = load_config(str(config_yaml), str(env_file))

    assert config.poll_interval_seconds == 60
    assert config.poll_jitter_fraction == 0.1
    assert config.telegram_bot_token == "abc123"
    assert config.telegram_chat_id == "999"
    assert config.registration_password == "let-me-in"


def test_load_config_defaults_poll_interval_to_five_minutes(tmp_path):
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text("")
    env_file = write_env(tmp_path)

    config = load_config(str(config_yaml), str(env_file))

    assert config.poll_interval_seconds == 300


def test_load_config_does_not_require_a_searches_list(tmp_path):
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text("db_path: custom.sqlite3\n")
    env_file = write_env(tmp_path)

    config = load_config(str(config_yaml), str(env_file))

    assert config.db_path == "custom.sqlite3"


def test_load_config_raises_when_missing_telegram_env(tmp_path):
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text("")
    env_file = tmp_path / ".env"
    env_file.write_text("REGISTRATION_PASSWORD=let-me-in\n")

    with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN"):
        load_config(str(config_yaml), str(env_file))


def test_load_config_raises_when_missing_registration_password(tmp_path):
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text("")
    env_file = tmp_path / ".env"
    env_file.write_text("TELEGRAM_BOT_TOKEN=abc123\nTELEGRAM_CHAT_ID=999\n")

    with pytest.raises(ValueError, match="REGISTRATION_PASSWORD"):
        load_config(str(config_yaml), str(env_file))


def test_process_environment_takes_precedence_over_env_file(tmp_path, monkeypatch):
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text("")
    env_file = write_env(tmp_path, token="from-file", chat_id="from-file", password="from-file")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "from-process")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "from-process")
    monkeypatch.setenv("REGISTRATION_PASSWORD", "from-process")

    config = load_config(str(config_yaml), str(env_file))

    assert config.telegram_bot_token == "from-process"
    assert config.registration_password == "from-process"


def test_process_environment_alone_is_sufficient(tmp_path, monkeypatch):
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text("")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "abc123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "999")
    monkeypatch.setenv("REGISTRATION_PASSWORD", "let-me-in")

    config = load_config(str(config_yaml), str(tmp_path / "does-not-exist.env"))

    assert config.telegram_bot_token == "abc123"
    assert config.registration_password == "let-me-in"
