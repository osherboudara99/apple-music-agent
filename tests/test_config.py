import os

import pytest

from music_agent import config as cfg


def test_data_dir_uses_env_override(isolated_home):
    assert cfg.data_dir() == isolated_home
    assert cfg.db_path() == isolated_home / "plays.db"
    assert cfg.config_path() == isolated_home / "config.toml"
    assert cfg.log_dir() == isolated_home / "logs"


def test_defaults_when_no_file(monkeypatch):
    monkeypatch.setattr(cfg, "system_timezone", lambda: "Europe/Berlin")
    c = cfg.load_config()
    assert c.timezone == "Europe/Berlin"
    assert c.model == "claude-haiku-4-5"
    assert c.monthly_budget_usd == 5.0
    assert c.telegram_allowed_user_id is None
    assert c.snapshot_interval_minutes == 10
    assert c.playlist_folder == "Music Agent"
    assert str(c.tz) == "Europe/Berlin"


def test_save_then_load_round_trip():
    original = cfg.Config(
        timezone="America/Los_Angeles",
        model="claude-sonnet-5",
        monthly_budget_usd=12.5,
        telegram_allowed_user_id=42,
    )
    cfg.save_config(original)
    assert cfg.load_config() == original


def test_none_values_are_not_written():
    cfg.save_config(cfg.Config(timezone="UTC"))
    assert "telegram_allowed_user_id" not in cfg.config_path().read_text()


def test_unknown_key_is_an_error():
    cfg.config_path().parent.mkdir(parents=True, exist_ok=True)
    cfg.config_path().write_text('timezone = "UTC"\nmodle = "x"\n')
    with pytest.raises(cfg.ConfigError, match="modle"):
        cfg.load_config()


def test_invalid_timezone_is_an_error():
    cfg.config_path().parent.mkdir(parents=True, exist_ok=True)
    cfg.config_path().write_text('timezone = "Mars/Olympus"\n')
    with pytest.raises(cfg.ConfigError, match="Mars/Olympus"):
        cfg.load_config()


def test_wrong_type_is_an_error():
    cfg.config_path().parent.mkdir(parents=True, exist_ok=True)
    cfg.config_path().write_text('timezone = "UTC"\nmonthly_budget_usd = "lots"\n')
    with pytest.raises(cfg.ConfigError, match="monthly_budget_usd"):
        cfg.load_config()


def test_system_timezone_reads_symlink(tmp_path):
    zone_file = tmp_path / "zoneinfo" / "America" / "New_York"
    zone_file.parent.mkdir(parents=True)
    zone_file.write_text("")
    link = tmp_path / "localtime"
    os.symlink(zone_file, link)
    assert cfg.system_timezone(str(link)) == "America/New_York"


def test_system_timezone_falls_back_to_utc(tmp_path):
    assert cfg.system_timezone(str(tmp_path / "missing")) == "UTC"


def test_secret_from_keychain(fake_keyring):
    cfg.set_secret("anthropic_api_key", "sk-keychain")
    assert fake_keyring[("music-agent", "anthropic_api_key")] == "sk-keychain"
    assert cfg.get_secret("anthropic_api_key") == "sk-keychain"


def test_env_overrides_keychain(monkeypatch):
    cfg.set_secret("anthropic_api_key", "sk-keychain")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
    assert cfg.get_secret("anthropic_api_key") == "sk-env"


def test_missing_secret_is_none():
    assert cfg.get_secret("telegram_bot_token") is None


def test_unknown_secret_name_rejected():
    with pytest.raises(KeyError):
        cfg.get_secret("nope")
