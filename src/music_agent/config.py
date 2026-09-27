"""Configuration file, data paths and Keychain-backed secrets."""

from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import keyring
import tomli_w

KEYRING_SERVICE = "music-agent"
# Package-specific names on purpose: a developer's general ANTHROPIC_API_KEY (often a work
# key) must never be picked up and billed by music-agent.
SECRET_ENV = {
    "anthropic_api_key": "MUSIC_AGENT_ANTHROPIC_API_KEY",
    "telegram_bot_token": "MUSIC_AGENT_TELEGRAM_BOT_TOKEN",
}


class ConfigError(Exception):
    """config.toml is invalid."""


@dataclass(frozen=True)
class Config:
    timezone: str
    model: str = "claude-haiku-4-5"
    monthly_budget_usd: float = 5.0
    telegram_allowed_user_id: int | None = None
    snapshot_interval_minutes: int = 10
    playlist_folder: str = "Music Agent"

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


_TYPES = {
    "timezone": str,
    "model": str,
    "monthly_budget_usd": float,
    "telegram_allowed_user_id": int,
    "snapshot_interval_minutes": int,
    "playlist_folder": str,
}


def data_dir() -> Path:
    override = os.environ.get("MUSIC_AGENT_HOME")
    if override:
        return Path(override)
    return Path.home() / "Library" / "Application Support" / "music-agent"


def config_path() -> Path:
    return data_dir() / "config.toml"


def db_path() -> Path:
    return data_dir() / "plays.db"


def log_dir() -> Path:
    return data_dir() / "logs"


def system_timezone(localtime: str = "/etc/localtime") -> str:
    """IANA zone name of the Mac's current time zone, or "UTC" if unknown."""
    target = os.path.realpath(localtime)
    marker = "zoneinfo/"
    if marker in target:
        name = target.split(marker, 1)[1]
        try:
            ZoneInfo(name)
            return name
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return "UTC"


def load_config() -> Config:
    path = config_path()
    raw = tomllib.loads(path.read_text()) if path.exists() else {}
    known = {f.name for f in fields(Config)}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ConfigError(f"Unknown keys in {path}: {', '.join(unknown)}")
    raw.setdefault("timezone", system_timezone())
    values = {}
    for key, value in raw.items():
        expected = _TYPES[key]
        try:
            values[key] = expected(value)
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"{key} in {path} must be {expected.__name__}, got {value!r}") from exc
    try:
        ZoneInfo(values["timezone"])
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(f"Unknown time zone {values['timezone']!r} in {path}") from exc
    return Config(**values)


def save_config(config: Config) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {k: v for k, v in asdict(config).items() if v is not None}
    path.write_text(tomli_w.dumps(data))


def get_secret(name: str) -> str | None:
    env_value = os.environ.get(SECRET_ENV[name])
    if env_value:
        return env_value
    return keyring.get_password(KEYRING_SERVICE, name)


def set_secret(name: str, value: str) -> None:
    SECRET_ENV[name]  # raises KeyError for unknown names
    keyring.set_password(KEYRING_SERVICE, name, value)
