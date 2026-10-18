# music-agent Phase 1 (Core, CLI, Telegram Bot) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `music-agent` package so a developer can record their Apple Music plays, ask questions and create playlists from the terminal (`chat`, `ask`), and optionally from Telegram (`run`).

**Architecture:**
- **Data:**
  - `music.py` is the only module that talks to Music.app (via JXA scripts).
  - A snapshot job compares play counts over time and writes play events to SQLite (`store.py`).
  - `queries.py` answers stats questions from SQLite.
- **Agent:**
  - `tools.py` exposes six tools.
  - `agent.py` runs a hand-written tool-use loop over `client.messages.create`, and saves history and usage.
- **Front ends:** `cli.py` (terminal) and `bot.py` (Telegram) both call the same `Agent`.

**Tech Stack:** Python ≥ 3.11, uv, `anthropic` 1.x (Messages API, `claude-haiku-4-5`), `python-telegram-bot` 22.x, `keyring`, `tomli-w`, SQLite (stdlib), JXA via `osascript`, pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-09-27-apple-music-agent-design.md`. This plan covers **Phase 1** only. Phase 2 (`setup`, `doctor`, `service`, docs, CI, release) gets its own plan.

## Global Constraints

- macOS only. `cli.main` exits with code 2 and a clear message when `sys.platform != "darwin"`.
- `requires-python = ">=3.11"`. Package name `music-agent`, module `music_agent` under `src/`, console script `music-agent = "music_agent.cli:main"`.
- Runtime dependencies: `anthropic>=1.8,<2`, `python-telegram-bot>=22.8,<23`, `keyring>=25.7`, `tomli-w>=1.2`. Dev: `pytest>=9.1`, `ruff>=0.16`. Add no other runtime dependencies.
- Config defaults:
  - `model = "claude-haiku-4-5"`
  - `monthly_budget_usd = 5.0`
  - `snapshot_interval_minutes = 10`
  - `playlist_folder = "Music Agent"`
  - `timezone` detected from the system.
- Data folder: `~/Library/Application Support/music-agent/` (`config.toml`, `plays.db`, `logs/`). The `MUSIC_AGENT_HOME` environment variable overrides it (tests and development).
- Secrets:
  - Keychain service `music-agent`, names `anthropic_api_key` and `telegram_bot_token`.
  - The environment variables `ANTHROPIC_API_KEY` / `TELEGRAM_BOT_TOKEN` override the Keychain.
- Times are stored as UTC ISO-8601 with seconds precision (`2026-09-27T18:43:23+00:00`). They are displayed in the configured time zone.
- No tool edits or deletes existing playlists or tracks.
- Tests never touch the real Music library unless marked `@pytest.mark.mac`. Any mac test that writes to the library must clean up in `finally`.
- Commits: conventional prefixes (`feat:`, `test:`, `chore:`, `docs:`, …), one per task at minimum. **No `Co-Authored-By` or other AI attribution lines.**
- Run everything through uv: `uv run pytest …`, `uv run ruff check`.

## Review Focus

1. **Music.app transiently returns 0 tracks** (iCloud library reloading). The snapshot must record an error and change nothing, and must not mark the whole library as removed. The test is in Task 6.
2. **An answer longer than Telegram's 4,096-character limit** (e.g. a long track list) must be split into several messages rather than failing. The test is in Task 13.
3. **Claude asks for a playlist containing IDs that are no longer in the library.** The playlist is created with the tracks that exist, and the missing IDs are reported, not raised as an error. The tests are in Tasks 8 and 10.
4. **Search text containing SQL wildcard characters** (`100%`, `_`, quotes) must match literally. The test is in Task 9.
5. **A Claude API failure in the middle of a tool loop** must leave no half-finished turn in history, so the next message works normally. The test is in Task 11.

---

## File Structure

```
pyproject.toml                         # package metadata, deps, script, pytest/ruff config
README.md                              # Phase 1 developer quickstart (Task 14)
src/music_agent/
  __init__.py                          # __version__
  models.py                            # Track, PlayEvent, utcnow()
  config.py                            # Config, load/save, Keychain secrets, data paths
  genres.py                            # genre → family mapping
  periods.py                           # named/explicit time windows → Period
  store.py                             # SQLite schema + helpers (tracks, plays, snapshots, meta, history, usage)
  snapshot.py                          # diff() + run_snapshot() + maybe_snapshot()
  music.py                             # JXA runner, read_library(), create_playlist()
  jxa/read_library.js
  jxa/create_playlist.js
  queries.py                           # stats and track lists over the store
  tools.py                             # ToolSpec, ToolContext, Toolbox (six tools)
  agent.py                             # Agent loop, system prompt, pricing, budget
  cli.py                               # argparse entry point: chat, ask, snapshot, status, run
  bot.py                               # BotLogic, Telegram wiring, SnapshotScheduler
tests/
  __init__.py
  conftest.py                          # isolated MUSIC_AGENT_HOME + fake keyring (autouse)
  factories.py                         # dt(), make_track()
  jxa/delete_playlist.js               # test-only cleanup for mac tests
  test_scaffold.py test_config.py test_genres.py test_periods.py test_store.py
  test_snapshot_diff.py test_snapshot_run.py test_music.py test_music_mac.py
  test_queries.py test_tools.py test_agent.py test_cli.py test_bot.py
```

---

### Task 0: Project scaffold

**Files:**
- Modify: `pyproject.toml`
- Delete: `main.py`
- Create: `src/music_agent/__init__.py`, `tests/__init__.py`, `tests/conftest.py`, `tests/test_scaffold.py`

**Interfaces:**
- Produces: importable package `music_agent` with `__version__: str`; autouse fixtures `isolated_home` (sets `MUSIC_AGENT_HOME` to a temp dir and returns that `Path`) and `fake_keyring` (in-memory `dict[(service, name)] -> value`).

- [ ] **Step 1: Replace `pyproject.toml`**

```toml
[project]
name = "music-agent"
version = "0.1.0"
description = "Ask questions about your Apple Music listening and build playlists, from the terminal or Telegram (macOS)."
readme = "README.md"
license = "MIT"
requires-python = ">=3.11"
dependencies = [
    "anthropic>=1.8,<2",
    "python-telegram-bot>=22.8,<23",
    "keyring>=25.7",
    "tomli-w>=1.2",
]

[project.scripts]
music-agent = "music_agent.cli:main"

[build-system]
requires = ["uv_build>=0.11,<0.12"]
build-backend = "uv_build"

[dependency-groups]
dev = ["pytest>=9.1", "ruff>=0.16"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["mac: needs a real Music.app library (opt-in: uv run pytest -m mac)"]
addopts = "-m 'not mac'"

[tool.ruff]
line-length = 100
target-version = "py311"
```

- [ ] **Step 2: Delete the placeholder and create the package**

```bash
git rm main.py
mkdir -p src/music_agent tests
```

`src/music_agent/__init__.py`:

```python
"""music-agent: ask questions about your Apple Music listening (macOS)."""

from importlib.metadata import version

__version__ = version("music-agent")
```

`tests/__init__.py`: empty file.

`tests/conftest.py`:

```python
import keyring
import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Point the data folder at a temp dir and clear secret env vars."""
    home = tmp_path / "home"
    monkeypatch.setenv("MUSIC_AGENT_HOME", str(home))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    return home


@pytest.fixture(autouse=True)
def fake_keyring(monkeypatch):
    """Replace the macOS Keychain with an in-memory dict."""
    secrets: dict[tuple[str, str], str] = {}
    monkeypatch.setattr(keyring, "get_password", lambda service, name: secrets.get((service, name)))
    monkeypatch.setattr(
        keyring, "set_password", lambda service, name, value: secrets.__setitem__((service, name), value)
    )
    return secrets
```

- [ ] **Step 3: Write the failing test**

`tests/test_scaffold.py`:

```python
import os

import music_agent


def test_version_matches_pyproject():
    assert music_agent.__version__ == "0.1.0"


def test_home_is_isolated(isolated_home):
    assert os.environ["MUSIC_AGENT_HOME"] == str(isolated_home)
```

- [ ] **Step 4: Install and run**

Run: `uv sync && uv run pytest -v`
Expected: 2 passed. If `uv sync` fails to build, re-check that `src/music_agent/__init__.py` exists; uv_build expects `src/<module_name>/`.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock src tests
git commit -m "chore: scaffold music-agent package and test setup"
```

---

### Task 1: Config and secrets

**Files:**
- Create: `src/music_agent/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces:
  - `class ConfigError(Exception)`
  - `@dataclass(frozen=True) class Config` with fields `timezone: str`, `model: str = "claude-haiku-4-5"`, `monthly_budget_usd: float = 5.0`, `telegram_allowed_user_id: int | None = None`, `snapshot_interval_minutes: int = 10`, `playlist_folder: str = "Music Agent"`, and property `tz -> ZoneInfo`
  - `data_dir() -> Path`, `config_path() -> Path`, `db_path() -> Path`, `log_dir() -> Path`
  - `system_timezone(localtime: str = "/etc/localtime") -> str`
  - `load_config() -> Config`, `save_config(config: Config) -> None`
  - `SECRET_ENV: dict[str, str]`, `get_secret(name: str) -> str | None`, `set_secret(name: str, value: str) -> None`

- [ ] **Step 1: Write the failing tests**

`tests/test_config.py`:

```python
import os
from pathlib import Path

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
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL with `ImportError: cannot import name 'config'`.

- [ ] **Step 3: Implement**

`src/music_agent/config.py`:

```python
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
SECRET_ENV = {
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "telegram_bot_token": "TELEGRAM_BOT_TOKEN",
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
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_config.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/music_agent/config.py tests/test_config.py
git commit -m "feat: add config file and keychain secrets"
```

---

### Task 2: Genre families

**Files:**
- Create: `src/music_agent/genres.py`
- Test: `tests/test_genres.py`

**Interfaces:**
- Produces: `FAMILY_KEYWORDS: dict[str, tuple[str, ...]]`, `families_of(genre: str) -> list[str]`, `family_of(genre: str) -> str`, `matches(genre: str, family: str | None = None, genres: list[str] | None = None) -> bool`.

- [ ] **Step 1: Write the failing tests**

`tests/test_genres.py`:

```python
import pytest

from music_agent.genres import families_of, family_of, matches


@pytest.mark.parametrize(
    ("genre", "family"),
    [
        ("Rock", "rock"),
        ("Hard Rock", "rock"),
        ("Alternative", "rock"),
        ("Metal", "rock"),
        ("Pop Punk", "rock"),
        ("Hip-Hop/Rap", "hip-hop"),
        ("Hip-Hop", "hip-hop"),
        ("Trap", "hip-hop"),
        ("Electronic", "electronic"),
        ("Dance", "electronic"),
        ("R&B/Soul", "r&b"),
        ("K-Pop", "pop"),
        ("Singer/Songwriter", "folk"),
        ("Dancehall", "reggae"),
        ("Worldwide", "worldwide"),
        ("", ""),
    ],
)
def test_family_of(genre, family):
    assert family_of(genre) == family


def test_keywords_match_whole_words_only():
    assert "hip-hop" not in families_of("Trapeze")  # "trap" inside a longer word
    assert "electronic" not in families_of("Dancehall")
    assert "rock" not in families_of("Memo")  # "emo" inside a word


def test_pop_punk_is_in_both_families():
    assert families_of("Pop Punk") == ["rock", "pop"]


def test_matches_no_filter_is_true():
    assert matches("Jazz")


def test_matches_family_case_insensitive():
    assert matches("Hard Rock", family="ROCK")
    assert not matches("Jazz", family="rock")


def test_matches_unknown_genre_by_its_own_name():
    assert matches("Worldwide", family="worldwide")


def test_matches_exact_genre_list():
    assert matches("Hard Rock", genres=["hard rock", "Metal"])
    assert not matches("Rock", genres=["Hard Rock"])


def test_matches_family_or_genres():
    assert matches("Jazz", family="rock", genres=["Jazz"])
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_genres.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`src/music_agent/genres.py`:

```python
"""Group Music.app's granular genres into broad families (rock, hip-hop, ...)."""

from __future__ import annotations

import re

# Order matters: family_of() returns the first matching family.
FAMILY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "rock": ("rock", "metal", "punk", "grunge", "alternative", "emo", "hardcore"),
    "hip-hop": ("hip-hop", "hip hop", "rap", "trap"),
    "electronic": ("electronic", "dance", "house", "techno", "edm", "trance", "drum & bass", "dubstep"),
    "r&b": ("r&b", "soul", "funk"),
    "pop": ("pop",),
    "jazz": ("jazz",),
    "blues": ("blues",),
    "country": ("country",),
    "classical": ("classical", "opera"),
    "folk": ("folk", "singer/songwriter"),
    "latin": ("latin", "reggaeton"),
    "reggae": ("reggae", "dancehall"),
    "soundtrack": ("soundtrack", "score"),
}

_PATTERNS = {
    family: [re.compile(rf"(?<![a-z]){re.escape(kw)}(?![a-z])") for kw in keywords]
    for family, keywords in FAMILY_KEYWORDS.items()
}


def families_of(genre: str) -> list[str]:
    g = genre.strip().lower()
    return [family for family, patterns in _PATTERNS.items() if any(p.search(g) for p in patterns)]


def family_of(genre: str) -> str:
    families = families_of(genre)
    return families[0] if families else genre.strip().lower()


def matches(genre: str, family: str | None = None, genres: list[str] | None = None) -> bool:
    """True if no filter is given, or the genre is in `genres`, or belongs to `family`."""
    if not family and not genres:
        return True
    g = genre.strip().lower()
    if genres and g in {x.strip().lower() for x in genres}:
        return True
    if family:
        f = family.strip().lower()
        return f in families_of(genre) or g == f
    return False
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_genres.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/music_agent/genres.py tests/test_genres.py
git commit -m "feat: add genre family mapping"
```

---

### Task 3: Time periods

**Files:**
- Create: `src/music_agent/periods.py`
- Test: `tests/test_periods.py`

**Interfaces:**
- Produces:
  - `PERIOD_NAMES: tuple[str, ...]`
  - `class PeriodError(ValueError)`
  - `@dataclass(frozen=True) class Period(start: datetime, end: datetime, label: str)`, where `start`/`end` are timezone-aware UTC and `end` is exclusive
  - `resolve(period: str | None, start: str | None, end: str | None, tz: ZoneInfo, now: datetime) -> Period`

- [ ] **Step 1: Write the failing tests**

`tests/test_periods.py`:

```python
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from music_agent.periods import PERIOD_NAMES, PeriodError, resolve

LA = ZoneInfo("America/Los_Angeles")
UTC = timezone.utc


def utc(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


def test_period_names():
    assert PERIOD_NAMES == ("today", "last_24h", "this_week", "past_week", "this_month", "this_year")


def test_today_starts_at_local_midnight():
    now = utc("2026-09-27T18:36:00")  # 11:36 PDT, Sunday
    p = resolve("today", None, None, LA, now)
    assert p.start == utc("2026-09-27T07:00:00")
    assert p.end == now
    assert p.label == "today"


def test_last_24h_and_past_week_are_rolling():
    now = utc("2026-09-27T18:36:00")
    assert resolve("last_24h", None, None, LA, now).start == now - timedelta(hours=24)
    assert resolve("past_week", None, None, LA, now).start == now - timedelta(days=7)


def test_this_week_on_sunday_goes_back_to_monday():
    now = utc("2026-09-27T18:36:00")  # Sunday
    assert resolve("this_week", None, None, LA, now).start == utc("2026-09-21T07:00:00")


def test_this_week_just_after_monday_midnight():
    now = utc("2026-09-28T07:30:00")  # Monday 00:30 PDT
    assert resolve("this_week", None, None, LA, now).start == utc("2026-09-28T07:00:00")


def test_this_week_across_dst_start():
    now = utc("2026-03-08T19:00:00")  # Sunday 12:00 PDT; DST began 02:00 that day
    p = resolve("this_week", None, None, LA, now)
    assert p.start == utc("2026-03-02T08:00:00")  # Monday 00:00 PST
    assert resolve("today", None, None, LA, now).start == utc("2026-03-08T08:00:00")


def test_this_month():
    now = utc("2026-09-27T18:36:00")
    assert resolve("this_month", None, None, LA, now).start == utc("2026-09-01T07:00:00")


def test_this_year_just_after_new_year():
    now = utc("2027-01-01T08:10:00")  # 00:10 PST Jan 1
    assert resolve("this_year", None, None, LA, now).start == utc("2027-01-01T08:00:00")


def test_explicit_dates_end_is_inclusive_day():
    now = utc("2026-10-15T00:00:00")
    p = resolve(None, "2026-09-01", "2026-09-30", LA, now)
    assert p.start == utc("2026-09-01T07:00:00")
    assert p.end == utc("2026-10-01T07:00:00")


def test_explicit_start_only_ends_now():
    now = utc("2026-09-27T18:36:00")
    p = resolve(None, "2026-09-20T09:00", None, LA, now)
    assert p.start == utc("2026-09-20T16:00:00")
    assert p.end == now


def test_period_name_is_case_insensitive():
    now = utc("2026-09-27T18:36:00")
    assert resolve(" Today ", None, None, LA, now).label == "today"


@pytest.mark.parametrize(
    ("period", "start", "end", "message"),
    [
        ("yesteryear", None, None, "Unknown period"),
        ("today", "2026-09-01", None, "either"),
        (None, None, None, "Give a period"),
        (None, "2026-09-10", "2026-09-01", "after"),
        (None, "not-a-date", None, "not-a-date"),
    ],
)
def test_errors(period, start, end, message):
    with pytest.raises(PeriodError, match=message):
        resolve(period, start, end, LA, utc("2026-09-27T18:36:00"))
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_periods.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`src/music_agent/periods.py`:

```python
"""Turn "today", "past_week", explicit dates, ... into UTC [start, end) windows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

PERIOD_NAMES = ("today", "last_24h", "this_week", "past_week", "this_month", "this_year")


class PeriodError(ValueError):
    """The requested time window is invalid."""


@dataclass(frozen=True)
class Period:
    start: datetime  # aware, UTC
    end: datetime  # aware, UTC, exclusive
    label: str


def _utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc)


def _parse(value: str, tz: ZoneInfo, is_end: bool) -> datetime:
    text = value.strip()
    try:
        if len(text) == 10:
            day = date.fromisoformat(text)
            if is_end:
                day += timedelta(days=1)
            return _utc(datetime.combine(day, time(0), tzinfo=tz))
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise PeriodError(f"Could not read date {value!r}; use ISO format like 2026-09-01") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return _utc(parsed)


def resolve(
    period: str | None, start: str | None, end: str | None, tz: ZoneInfo, now: datetime
) -> Period:
    if period:
        if start or end:
            raise PeriodError("Pass either period or start/end, not both.")
        name = period.strip().lower()
        now_local = now.astimezone(tz)
        midnight = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        if name == "today":
            begin = midnight
        elif name == "last_24h":
            begin = now - timedelta(hours=24)
        elif name == "this_week":
            begin = midnight - timedelta(days=now_local.weekday())
        elif name == "past_week":
            begin = now - timedelta(days=7)
        elif name == "this_month":
            begin = midnight.replace(day=1)
        elif name == "this_year":
            begin = midnight.replace(month=1, day=1)
        else:
            raise PeriodError(
                f"Unknown period {period!r}. Use one of: {', '.join(PERIOD_NAMES)}, or start/end dates."
            )
        return Period(_utc(begin), _utc(now), name)
    if not start:
        raise PeriodError("Give a period or a start date.")
    begin = _parse(start, tz, is_end=False)
    finish = _parse(end, tz, is_end=True) if end else _utc(now)
    if finish <= begin:
        raise PeriodError("end must be after start.")
    return Period(begin, finish, f"{start}..{end or 'now'}")
```

Note: `midnight - timedelta(days=n)` is wall-clock arithmetic on an aware datetime, so the Monday-midnight result keeps hour 0. `astimezone` then applies that day's UTC offset, which is why the DST test passes.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_periods.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/music_agent/periods.py tests/test_periods.py
git commit -m "feat: add time period resolution"
```

---

### Task 4: Models and SQLite store

**Files:**
- Create: `src/music_agent/models.py`, `src/music_agent/store.py`, `tests/factories.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Produces:
  - `models.Track(persistent_id: str, name: str, artist: str, album: str, genre: str, duration_s: float, date_added: datetime | None, played_count: int, played_date: datetime | None)` (frozen)
  - `models.PlayEvent(persistent_id: str, played_at: datetime, window_start: datetime | None, detected_at: datetime, approx: bool, source: str = "snapshot")` (frozen)
  - `models.utcnow() -> datetime` (aware UTC, microseconds zeroed)
  - `store.to_iso(dt: datetime | None) -> str | None`, `store.from_iso(s: str | None) -> datetime | None`
  - `store.Store(path: Path)` with `.connect()` (context manager yielding a `sqlite3.Connection` with `row_factory=sqlite3.Row`) and `.transaction()` (context manager running `BEGIN IMMEDIATE`, then COMMIT, or ROLLBACK on exception)
  - Module functions that take a connection: `load_active_tracks(conn) -> dict[str, Track]`, `upsert_tracks(conn, tracks: list[Track], seen_at: datetime) -> None`, `mark_removed(conn, ids: list[str], at: datetime) -> None`, `insert_plays(conn, events: list[PlayEvent]) -> None`, `record_snapshot(conn, taken_at: datetime, track_count: int, events_added: int, duration_ms: int, error: str | None = None) -> None`, `last_snapshot_at(conn) -> datetime | None` (successful snapshots only), `get_meta(conn, key: str) -> str | None`, `set_meta(conn, key: str, value: str) -> None`
  - `tests/factories.py`: `dt(s: str) -> datetime` (a UTC string such as `"2026-09-27T18:00:00"` becomes an aware UTC datetime) and `make_track(pid="A", **overrides) -> Track`

- [ ] **Step 1: Write the models and factories**

`src/music_agent/models.py`:

```python
"""Plain data types shared across modules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class Track:
    persistent_id: str
    name: str
    artist: str
    album: str
    genre: str
    duration_s: float
    date_added: datetime | None
    played_count: int
    played_date: datetime | None


@dataclass(frozen=True)
class PlayEvent:
    persistent_id: str
    played_at: datetime
    window_start: datetime | None
    detected_at: datetime
    approx: bool
    source: str = "snapshot"


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)
```

`tests/factories.py`:

```python
from datetime import datetime, timezone

from music_agent.models import Track


def dt(s: str) -> datetime:
    """'2026-09-27T18:00:00' (UTC) -> aware UTC datetime."""
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def make_track(pid: str = "A", **overrides) -> Track:
    values = dict(
        persistent_id=pid,
        name=f"Song {pid}",
        artist="Artist",
        album="Album",
        genre="Rock",
        duration_s=200.0,
        date_added=None,
        played_count=0,
        played_date=None,
    )
    values.update(overrides)
    return Track(**values)
```

- [ ] **Step 2: Write the failing tests**

`tests/test_store.py`:

```python
import pytest

from music_agent import store as st
from music_agent.models import PlayEvent
from tests.factories import dt, make_track


@pytest.fixture
def store(isolated_home):
    return st.Store(isolated_home / "plays.db")


def test_iso_round_trip():
    value = dt("2026-09-27T18:43:23")
    assert st.to_iso(value) == "2026-09-27T18:43:23+00:00"
    assert st.from_iso(st.to_iso(value)) == value
    assert st.to_iso(None) is None and st.from_iso(None) is None


def test_schema_and_wal(store):
    with store.connect() as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"tracks", "plays", "snapshots", "meta", "conversations", "usage"} <= tables
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert st.get_meta(conn, "schema_version") == "1"


def test_upsert_and_load_active(store):
    a = make_track("A", name="Heretic", played_count=3, played_date=dt("2026-09-26T20:55:30"))
    with store.transaction() as conn:
        st.upsert_tracks(conn, [a, make_track("B")], dt("2026-09-27T00:00:00"))
    with store.connect() as conn:
        loaded = st.load_active_tracks(conn)
    assert loaded["A"] == a
    assert set(loaded) == {"A", "B"}


def test_unicode_round_trip(store):
    t = make_track("H", name="שוש אלמוזלינו", artist="Habiluim")
    with store.transaction() as conn:
        st.upsert_tracks(conn, [t], dt("2026-09-27T00:00:00"))
    with store.connect() as conn:
        assert st.load_active_tracks(conn)["H"].name == "שוש אלמוזלינו"


def test_removed_tracks_hidden_then_revived(store):
    with store.transaction() as conn:
        st.upsert_tracks(conn, [make_track("A")], dt("2026-09-27T00:00:00"))
        st.mark_removed(conn, ["A"], dt("2026-09-27T01:00:00"))
    with store.connect() as conn:
        assert st.load_active_tracks(conn) == {}
    with store.transaction() as conn:
        st.upsert_tracks(conn, [make_track("A")], dt("2026-09-27T02:00:00"))
    with store.connect() as conn:
        assert "A" in st.load_active_tracks(conn)


def test_insert_plays(store):
    event = PlayEvent("A", dt("2026-09-27T18:00:00"), None, dt("2026-09-27T18:05:00"), False)
    with store.transaction() as conn:
        st.insert_plays(conn, [event])
    with store.connect() as conn:
        row = conn.execute("SELECT * FROM plays").fetchone()
    assert row["persistent_id"] == "A"
    assert row["played_at"] == "2026-09-27T18:00:00+00:00"
    assert row["approx"] == 0 and row["source"] == "snapshot"


def test_transaction_rolls_back_on_error(store):
    with pytest.raises(RuntimeError):
        with store.transaction() as conn:
            st.upsert_tracks(conn, [make_track("A")], dt("2026-09-27T00:00:00"))
            raise RuntimeError("boom")
    with store.connect() as conn:
        assert st.load_active_tracks(conn) == {}


def test_last_snapshot_ignores_failed_runs(store):
    with store.transaction() as conn:
        assert st.last_snapshot_at(conn) is None
        st.record_snapshot(conn, dt("2026-09-27T10:00:00"), 5, 0, 12)
        st.record_snapshot(conn, dt("2026-09-27T10:10:00"), 0, 0, 3, error="Music.app failed")
    with store.connect() as conn:
        assert st.last_snapshot_at(conn) == dt("2026-09-27T10:00:00")


def test_meta(store):
    with store.transaction() as conn:
        st.set_meta(conn, "install_at", "x")
        st.set_meta(conn, "install_at", "y")
    with store.connect() as conn:
        assert st.get_meta(conn, "install_at") == "y"
        assert st.get_meta(conn, "missing") is None
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_store.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 4: Implement**

`src/music_agent/store.py`:

```python
"""SQLite storage. Each operation opens its own connection, so threads can share a Store."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .models import PlayEvent, Track

SCHEMA_VERSION = "1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    persistent_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    artist TEXT NOT NULL,
    album TEXT NOT NULL,
    genre TEXT NOT NULL,
    duration_s REAL NOT NULL,
    date_added TEXT,
    played_count INTEGER NOT NULL,
    played_date TEXT,
    last_seen_at TEXT NOT NULL,
    removed_at TEXT
);
CREATE INDEX IF NOT EXISTS tracks_played_date ON tracks(played_date);
CREATE TABLE IF NOT EXISTS plays (
    id INTEGER PRIMARY KEY,
    persistent_id TEXT NOT NULL,
    played_at TEXT NOT NULL,
    window_start TEXT,
    detected_at TEXT NOT NULL,
    approx INTEGER NOT NULL,
    source TEXT NOT NULL DEFAULT 'snapshot'
);
CREATE INDEX IF NOT EXISTS plays_played_at ON plays(played_at);
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY,
    taken_at TEXT NOT NULL,
    track_count INTEGER NOT NULL,
    events_added INTEGER NOT NULL,
    duration_ms INTEGER NOT NULL,
    error TEXT
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY,
    chat_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS conversations_chat ON conversations(chat_id, id);
CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cache_read_tokens INTEGER NOT NULL,
    cost_usd REAL NOT NULL
);
"""


def to_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def from_iso(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
                (SCHEMA_VERSION,),
            )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """BEGIN IMMEDIATE: takes the write lock up front, so concurrent writers serialize."""
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")


def _row_to_track(row: sqlite3.Row) -> Track:
    return Track(
        persistent_id=row["persistent_id"],
        name=row["name"],
        artist=row["artist"],
        album=row["album"],
        genre=row["genre"],
        duration_s=row["duration_s"],
        date_added=from_iso(row["date_added"]),
        played_count=row["played_count"],
        played_date=from_iso(row["played_date"]),
    )


def load_active_tracks(conn: sqlite3.Connection) -> dict[str, Track]:
    rows = conn.execute("SELECT * FROM tracks WHERE removed_at IS NULL")
    return {row["persistent_id"]: _row_to_track(row) for row in rows}


def upsert_tracks(conn: sqlite3.Connection, tracks: list[Track], seen_at: datetime) -> None:
    conn.executemany(
        """
        INSERT INTO tracks (persistent_id, name, artist, album, genre, duration_s, date_added,
                            played_count, played_date, last_seen_at, removed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
        ON CONFLICT(persistent_id) DO UPDATE SET
            name = excluded.name, artist = excluded.artist, album = excluded.album,
            genre = excluded.genre, duration_s = excluded.duration_s,
            date_added = excluded.date_added, played_count = excluded.played_count,
            played_date = excluded.played_date, last_seen_at = excluded.last_seen_at,
            removed_at = NULL
        """,
        [
            (
                t.persistent_id, t.name, t.artist, t.album, t.genre, t.duration_s,
                to_iso(t.date_added), t.played_count, to_iso(t.played_date), to_iso(seen_at),
            )
            for t in tracks
        ],
    )


def mark_removed(conn: sqlite3.Connection, ids: list[str], at: datetime) -> None:
    conn.executemany(
        "UPDATE tracks SET removed_at = ? WHERE persistent_id = ?",
        [(to_iso(at), pid) for pid in ids],
    )


def insert_plays(conn: sqlite3.Connection, events: list[PlayEvent]) -> None:
    conn.executemany(
        """
        INSERT INTO plays (persistent_id, played_at, window_start, detected_at, approx, source)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (
                e.persistent_id, to_iso(e.played_at), to_iso(e.window_start),
                to_iso(e.detected_at), int(e.approx), e.source,
            )
            for e in events
        ],
    )


def record_snapshot(
    conn: sqlite3.Connection,
    taken_at: datetime,
    track_count: int,
    events_added: int,
    duration_ms: int,
    error: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO snapshots (taken_at, track_count, events_added, duration_ms, error)
        VALUES (?, ?, ?, ?, ?)
        """,
        (to_iso(taken_at), track_count, events_added, duration_ms, error),
    )


def last_snapshot_at(conn: sqlite3.Connection) -> datetime | None:
    row = conn.execute(
        "SELECT taken_at FROM snapshots WHERE error IS NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return from_iso(row["taken_at"]) if row else None


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_store.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/music_agent/models.py src/music_agent/store.py tests/factories.py tests/test_store.py
git commit -m "feat: add sqlite store and data models"
```

---

### Task 5: Snapshot diff (pure logic)

**Files:**
- Create: `src/music_agent/snapshot.py`
- Test: `tests/test_snapshot_diff.py`

**Interfaces:**
- Consumes: `Track`, `PlayEvent` (Task 4).
- Produces: `@dataclass(frozen=True) DiffResult(events: list[PlayEvent], removed_ids: list[str], warnings: list[str])`; `diff(stored: dict[str, Track], live: list[Track], prev_snapshot_at: datetime | None, now: datetime) -> DiffResult`.

The rules come from the spec's "Snapshot algorithm" table:
- **First run** (`prev_snapshot_at is None`): no events.
- **Play count rose by N:** N events. The newest has `played_at = played_date`, `approx=False`, `window_start=None`. The other N−1 have the same `played_at`, `window_start=prev_snapshot_at` and `approx=True`. If `played_date` is missing or didn't advance, the newest is also approximate: `played_at = played_date or now`, `window_start=prev_snapshot_at`.
- **New track** with `played_count > 0` and `played_date > prev_snapshot_at`: 1 exact event. Other new tracks: nothing.
- **Play count decreased:** a warning and no events.
- **A stored ID missing from live:** it goes in `removed_ids`.

- [ ] **Step 1: Write the failing tests**

`tests/test_snapshot_diff.py`:

```python
from music_agent.models import PlayEvent
from music_agent.snapshot import diff
from tests.factories import dt, make_track

PREV = dt("2026-09-27T18:00:00")
NOW = dt("2026-09-27T18:10:00")


def test_first_run_records_no_events():
    live = [make_track("A", played_count=5, played_date=dt("2026-09-27T17:59:00"))]
    result = diff({}, live, None, NOW)
    assert result.events == [] and result.removed_ids == [] and result.warnings == []


def test_count_up_by_one_is_exact():
    old = make_track("A", played_count=5, played_date=dt("2026-09-26T10:00:00"))
    new = make_track("A", played_count=6, played_date=dt("2026-09-27T18:04:00"))
    result = diff({"A": old}, [new], PREV, NOW)
    assert result.events == [PlayEvent("A", dt("2026-09-27T18:04:00"), None, NOW, False)]


def test_count_up_by_three_gives_one_exact_two_approx():
    old = make_track("A", played_count=5, played_date=dt("2026-09-26T10:00:00"))
    new = make_track("A", played_count=8, played_date=dt("2026-09-27T18:09:00"))
    events = diff({"A": old}, [new], PREV, NOW).events
    assert len(events) == 3
    assert events[0] == PlayEvent("A", dt("2026-09-27T18:09:00"), None, NOW, False)
    for e in events[1:]:
        assert e == PlayEvent("A", dt("2026-09-27T18:09:00"), PREV, NOW, True)


def test_count_up_without_new_played_date_is_approx():
    old = make_track("A", played_count=5, played_date=dt("2026-09-27T17:00:00"))
    new = make_track("A", played_count=6, played_date=dt("2026-09-27T17:00:00"))
    assert diff({"A": old}, [new], PREV, NOW).events == [
        PlayEvent("A", dt("2026-09-27T17:00:00"), PREV, NOW, True)
    ]


def test_count_up_with_missing_played_date_uses_now():
    old = make_track("A", played_count=0)
    new = make_track("A", played_count=1, played_date=None)
    assert diff({"A": old}, [new], PREV, NOW).events == [PlayEvent("A", NOW, PREV, NOW, True)]


def test_new_track_played_since_last_snapshot():
    new = make_track("N", played_count=1, played_date=dt("2026-09-27T18:05:00"))
    assert diff({}, [new], PREV, NOW).events == [
        PlayEvent("N", dt("2026-09-27T18:05:00"), None, NOW, False)
    ]


def test_new_track_played_before_last_snapshot_is_baseline():
    new = make_track("N", played_count=4, played_date=dt("2026-09-20T00:00:00"))
    assert diff({}, [new], PREV, NOW).events == []


def test_new_unplayed_track_is_baseline():
    assert diff({}, [make_track("N")], PREV, NOW).events == []


def test_count_decrease_warns_and_records_nothing():
    old = make_track("A", name="Heretic", artist="A7X", played_count=9)
    new = make_track("A", name="Heretic", artist="A7X", played_count=2)
    result = diff({"A": old}, [new], PREV, NOW)
    assert result.events == []
    assert result.warnings == ["play count went down for A7X - Heretic (9 -> 2); resetting baseline"]


def test_unchanged_track_records_nothing():
    t = make_track("A", played_count=3, played_date=dt("2026-09-20T00:00:00"))
    assert diff({"A": t}, [t], PREV, NOW).events == []


def test_missing_tracks_are_removed():
    stored = {"A": make_track("A"), "B": make_track("B"), "C": make_track("C")}
    result = diff(stored, [make_track("B")], PREV, NOW)
    assert result.removed_ids == ["A", "C"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_snapshot_diff.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`src/music_agent/snapshot.py`:

```python
"""Turn successive library snapshots into play events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .models import PlayEvent, Track


@dataclass(frozen=True)
class DiffResult:
    events: list[PlayEvent]
    removed_ids: list[str]
    warnings: list[str]


def diff(
    stored: dict[str, Track], live: list[Track], prev_snapshot_at: datetime | None, now: datetime
) -> DiffResult:
    events: list[PlayEvent] = []
    warnings: list[str] = []
    live_ids: set[str] = set()
    for track in live:
        live_ids.add(track.persistent_id)
        if prev_snapshot_at is None:
            continue
        old = stored.get(track.persistent_id)
        if old is None:
            if (
                track.played_count > 0
                and track.played_date is not None
                and track.played_date > prev_snapshot_at
            ):
                events.append(PlayEvent(track.persistent_id, track.played_date, None, now, False))
            continue
        delta = track.played_count - old.played_count
        if delta < 0:
            warnings.append(
                f"play count went down for {track.artist} - {track.name} "
                f"({old.played_count} -> {track.played_count}); resetting baseline"
            )
            continue
        if delta == 0:
            continue
        exact = track.played_date is not None and (
            old.played_date is None or track.played_date > old.played_date
        )
        played_at = track.played_date or now
        if exact:
            events.append(PlayEvent(track.persistent_id, played_at, None, now, False))
        else:
            events.append(PlayEvent(track.persistent_id, played_at, prev_snapshot_at, now, True))
        for _ in range(delta - 1):
            events.append(PlayEvent(track.persistent_id, played_at, prev_snapshot_at, now, True))
    removed = sorted(set(stored) - live_ids)
    return DiffResult(events, removed, warnings)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_snapshot_diff.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/music_agent/snapshot.py tests/test_snapshot_diff.py
git commit -m "feat: add snapshot diff logic"
```

---

### Task 6: Running snapshots (transactional, concurrent-safe)

**Files:**
- Modify: `src/music_agent/snapshot.py` (append)
- Test: `tests/test_snapshot_run.py`

**Interfaces:**
- Consumes: `Store`, `load_active_tracks`, `upsert_tracks`, `mark_removed`, `insert_plays`, `record_snapshot`, `last_snapshot_at`, `set_meta`, `to_iso` (Task 4); `diff` (Task 5); `utcnow` (Task 4).
- Produces:
  - `@dataclass(frozen=True) SnapshotResult(taken_at: datetime, track_count: int, events_added: int, first_run: bool, warnings: list[str], error: str | None = None)`
  - `run_snapshot(store: Store, read_library: Callable[[], list[Track]], clock: Callable[[], datetime] = utcnow) -> SnapshotResult`
  - `maybe_snapshot(store: Store, read_library: Callable[[], list[Track]], max_age: timedelta = timedelta(seconds=60), clock: Callable[[], datetime] = utcnow) -> SnapshotResult | None` (returns `None` when the last successful snapshot is newer than `max_age`)

- [ ] **Step 1: Write the failing tests**

`tests/test_snapshot_run.py`:

```python
import threading
from datetime import timedelta
from itertools import count

import pytest

from music_agent import store as st
from music_agent.snapshot import maybe_snapshot, run_snapshot
from tests.factories import dt, make_track


@pytest.fixture
def store(isolated_home):
    return st.Store(isolated_home / "plays.db")


def ticking_clock(start="2026-09-27T18:00:00", step_seconds=60):
    ticks = count()
    base = dt(start)
    return lambda: base + timedelta(seconds=step_seconds * next(ticks))


def plays(store):
    with store.connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM plays").fetchone()[0]


def test_first_run_sets_install_and_baseline(store):
    clock = ticking_clock()
    result = run_snapshot(store, lambda: [make_track("A", played_count=5)], clock)
    assert result.first_run and result.events_added == 0 and result.track_count == 1
    with store.connect() as conn:
        assert st.get_meta(conn, "install_at") == "2026-09-27T18:00:00+00:00"
        assert st.last_snapshot_at(conn) == dt("2026-09-27T18:00:00")


def test_second_run_records_plays(store):
    clock = ticking_clock()
    run_snapshot(store, lambda: [make_track("A", played_count=5)], clock)
    live = [make_track("A", played_count=7, played_date=dt("2026-09-27T18:00:30"))]
    result = run_snapshot(store, lambda: live, clock)
    assert not result.first_run and result.events_added == 2
    assert plays(store) == 2


def test_read_error_is_recorded_and_changes_nothing(store):
    clock = ticking_clock()
    run_snapshot(store, lambda: [make_track("A", played_count=5)], clock)

    def broken():
        raise RuntimeError("Music.app script failed")

    result = run_snapshot(store, broken, clock)
    assert result.error == "Music.app script failed"
    with store.connect() as conn:
        assert st.last_snapshot_at(conn) == dt("2026-09-27T18:00:00")
        assert "A" in st.load_active_tracks(conn)


def test_empty_library_read_is_an_error_not_a_mass_removal(store):
    clock = ticking_clock()
    run_snapshot(store, lambda: [make_track("A"), make_track("B")], clock)
    result = run_snapshot(store, lambda: [], clock)
    assert result.error is not None and "0 tracks" in result.error
    with store.connect() as conn:
        assert set(st.load_active_tracks(conn)) == {"A", "B"}


def test_overlapping_snapshots_count_each_play_once(store):
    clock = ticking_clock()
    run_snapshot(store, lambda: [make_track("A", played_count=5)], clock)
    live = [make_track("A", played_count=7, played_date=dt("2026-09-27T18:00:30"))]
    barrier = threading.Barrier(2)

    def read_after_both_have_read():
        barrier.wait(timeout=5)  # both threads hold the same live data before either writes
        return live

    threads = [
        threading.Thread(target=run_snapshot, args=(store, read_after_both_have_read, clock))
        for _ in range(2)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert plays(store) == 2


def test_maybe_snapshot_skips_when_recent(store):
    run_snapshot(store, lambda: [make_track("A")], lambda: dt("2026-09-27T18:00:00"))
    calls = []

    def read():
        calls.append(1)
        return [make_track("A")]

    assert maybe_snapshot(store, read, clock=lambda: dt("2026-09-27T18:00:30")) is None
    assert calls == []
    assert maybe_snapshot(store, read, clock=lambda: dt("2026-09-27T18:02:00")) is not None
    assert calls == [1]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_snapshot_run.py -v`
Expected: FAIL with `ImportError: cannot import name 'maybe_snapshot'`.

- [ ] **Step 3: Implement (append to `src/music_agent/snapshot.py`)**

Add these imports at the top of the file, merging them with the existing ones:

```python
import logging
import time
from collections.abc import Callable
from datetime import timedelta

from . import store as st
from .models import utcnow
```

Append:

```python
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SnapshotResult:
    taken_at: datetime
    track_count: int
    events_added: int
    first_run: bool
    warnings: list[str]
    error: str | None = None


def _record_failure(store: st.Store, at: datetime, started: float, error: str) -> SnapshotResult:
    with store.transaction() as conn:
        st.record_snapshot(conn, at, 0, 0, int((time.monotonic() - started) * 1000), error=error)
    log.error("snapshot failed: %s", error)
    return SnapshotResult(at, 0, 0, False, [], error=error)


def run_snapshot(
    store: st.Store,
    read_library: Callable[[], list[Track]],
    clock: Callable[[], datetime] = utcnow,
) -> SnapshotResult:
    started = time.monotonic()
    try:
        live = read_library()
    except Exception as exc:  # noqa: BLE001 - any read failure is recorded, not raised
        return _record_failure(store, clock(), started, str(exc))

    with store.transaction() as conn:
        now = clock()
        stored = st.load_active_tracks(conn)
        if not live and stored:
            error = "Music.app returned 0 tracks; skipping this snapshot"
            st.record_snapshot(conn, now, 0, 0, int((time.monotonic() - started) * 1000), error)
            log.error("snapshot failed: %s", error)
            return SnapshotResult(now, 0, 0, False, [], error=error)
        prev = st.last_snapshot_at(conn)
        result = diff(stored, live, prev, now)
        st.upsert_tracks(conn, live, now)
        st.mark_removed(conn, result.removed_ids, now)
        st.insert_plays(conn, result.events)
        if prev is None:
            st.set_meta(conn, "install_at", st.to_iso(now))
        duration_ms = int((time.monotonic() - started) * 1000)
        st.record_snapshot(conn, now, len(live), len(result.events), duration_ms)
    for warning in result.warnings:
        log.warning(warning)
    return SnapshotResult(now, len(live), len(result.events), prev is None, result.warnings)


def maybe_snapshot(
    store: st.Store,
    read_library: Callable[[], list[Track]],
    max_age: timedelta = timedelta(seconds=60),
    clock: Callable[[], datetime] = utcnow,
) -> SnapshotResult | None:
    with store.connect() as conn:
        last = st.last_snapshot_at(conn)
    if last is not None and clock() - last < max_age:
        return None
    return run_snapshot(store, read_library, clock)
```

This is safe under concurrency because the stored rows are read *inside* `BEGIN IMMEDIATE`. The second writer waits for the first to commit, then sees the updated counts and finds no remaining difference.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_snapshot_run.py tests/test_snapshot_diff.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/music_agent/snapshot.py tests/test_snapshot_run.py
git commit -m "feat: run snapshots transactionally with failure handling"
```

---

### Task 7: Reading the library from Music.app

**Files:**
- Create: `src/music_agent/music.py`, `src/music_agent/jxa/read_library.js`
- Test: `tests/test_music.py`, `tests/test_music_mac.py`

**Interfaces:**
- Consumes: `Track` (Task 4).
- Produces:
  - `class MusicError(RuntimeError)` with `.code: int | None` and property `.permission_denied: bool` (`code == -1743`)
  - `JXA_DIR: Path`
  - `Runner = Callable[[list[str]], subprocess.CompletedProcess]`
  - `run_jxa(script: str, *args: str, runner: Runner | None = None) -> str`
  - `parse_tracks(payload: str) -> list[Track]`
  - `read_library(runner: Runner | None = None) -> list[Track]`

- [ ] **Step 1: Write the JXA script**

`src/music_agent/jxa/read_library.js`:

```javascript
// Bulk-read every library track as a JSON array. One Apple Event per property, not per track.
function run(argv) {
  const Music = Application('Music');
  const tracks = Music.libraryPlaylists[0].tracks;
  let ids;
  try {
    ids = tracks.persistentID();
  } catch (e) {
    if (e.errorNumber === -1728) return '[]'; // empty library
    throw e;
  }
  const names = tracks.name(), artists = tracks.artist(), albums = tracks.album();
  const genres = tracks.genre(), durations = tracks.duration(), added = tracks.dateAdded();
  const counts = tracks.playedCount(), played = tracks.playedDate();
  const iso = (d) => (d ? d.toISOString() : null);
  return JSON.stringify(ids.map((id, i) => ({
    persistent_id: id,
    name: names[i] || '',
    artist: artists[i] || '',
    album: albums[i] || '',
    genre: genres[i] || '',
    duration_s: durations[i] || 0,
    date_added: iso(added[i]),
    played_count: counts[i] || 0,
    played_date: iso(played[i]),
  })));
}
```

- [ ] **Step 2: Write the failing unit tests**

`tests/test_music.py`:

```python
import json
import subprocess

import pytest

from music_agent import music
from tests.factories import dt


def fake_runner(stdout="", stderr="", returncode=0, calls=None):
    def run(args):
        if calls is not None:
            calls.append(args)
        return subprocess.CompletedProcess(args, returncode, stdout, stderr)

    return run


SAMPLE = json.dumps(
    [
        {
            "persistent_id": "3385DCEE8B3BB7F9",
            "name": "שוש אלמוזלינו",
            "artist": "Habiluim",
            "album": "",
            "genre": "Rock",
            "duration_s": 201.5,
            "date_added": "2024-01-02T03:04:05.000Z",
            "played_count": 3,
            "played_date": "2026-09-27T18:43:23.000Z",
        },
        {
            "persistent_id": "0000000000000001",
            "name": "Never Played",
            "artist": "X",
            "album": "Y",
            "genre": "",
            "duration_s": 0,
            "date_added": None,
            "played_count": 0,
            "played_date": None,
        },
    ],
    ensure_ascii=False,
)


def test_parse_tracks():
    first, second = music.parse_tracks(SAMPLE)
    assert first.persistent_id == "3385DCEE8B3BB7F9"
    assert first.name == "שוש אלמוזלינו"
    assert first.played_date == dt("2026-09-27T18:43:23")
    assert first.date_added == dt("2024-01-02T03:04:05")
    assert second.played_date is None and second.date_added is None
    assert second.played_count == 0


def test_read_library_calls_osascript_with_script():
    calls = []
    tracks = music.read_library(runner=fake_runner(stdout=SAMPLE, calls=calls))
    assert len(tracks) == 2
    args = calls[0]
    assert args[:3] == ["osascript", "-l", "JavaScript"]
    assert args[3].endswith("jxa/read_library.js")


def test_permission_denied_error():
    stderr = "read_library.js: execution error: Not authorized to send Apple events to Music. (-1743)"
    with pytest.raises(music.MusicError) as info:
        music.run_jxa("read_library.js", runner=fake_runner(stderr=stderr, returncode=1))
    assert info.value.code == -1743
    assert info.value.permission_denied
    assert "Automation" in str(info.value)


def test_generic_error_keeps_message():
    stderr = "execution error: Error: Can't get object. (-1728)"
    with pytest.raises(music.MusicError) as info:
        music.run_jxa("read_library.js", runner=fake_runner(stderr=stderr, returncode=1))
    assert info.value.code == -1728
    assert "Can't get object" in str(info.value)


def test_error_without_code():
    with pytest.raises(music.MusicError) as info:
        music.run_jxa("read_library.js", runner=fake_runner(stderr="", returncode=1))
    assert info.value.code is None
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_music.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 4: Implement**

`src/music_agent/music.py`:

```python
"""The only module that talks to Music.app (via JXA scripts run by osascript)."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from .models import Track

JXA_DIR = Path(__file__).parent / "jxa"
Runner = Callable[[list[str]], subprocess.CompletedProcess]

_CODE_RE = re.compile(r"\((-?\d+)\)\s*$")
PERMISSION_HELP = (
    "macOS blocked access to Music.app. Allow it in System Settings > Privacy & Security > "
    "Automation (turn on Music under your terminal app or music-agent), then try again."
)


class MusicError(RuntimeError):
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code

    @property
    def permission_denied(self) -> bool:
        return self.code == -1743


def _default_runner(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=300)


def run_jxa(script: str, *args: str, runner: Runner | None = None) -> str:
    run = runner or _default_runner
    proc = run(["osascript", "-l", "JavaScript", str(JXA_DIR / script), *args])
    if proc.returncode != 0:
        text = (proc.stderr or "").strip()
        match = _CODE_RE.search(text)
        code = int(match.group(1)) if match else None
        if code == -1743:
            raise MusicError(PERMISSION_HELP, code)
        raise MusicError(f"Music.app script failed: {text or 'no error output'}", code)
    return proc.stdout


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value).astimezone(timezone.utc).replace(microsecond=0)


def parse_tracks(payload: str) -> list[Track]:
    return [
        Track(
            persistent_id=item["persistent_id"],
            name=item["name"],
            artist=item["artist"],
            album=item["album"],
            genre=item["genre"],
            duration_s=float(item["duration_s"]),
            date_added=_parse_time(item["date_added"]),
            played_count=int(item["played_count"]),
            played_date=_parse_time(item["played_date"]),
        )
        for item in json.loads(payload)
    ]


def read_library(runner: Runner | None = None) -> list[Track]:
    return parse_tracks(run_jxa("read_library.js", runner=runner))
```

- [ ] **Step 5: Run unit tests**

Run: `uv run pytest tests/test_music.py -v`
Expected: all pass.

- [ ] **Step 6: Add the Mac integration test**

`tests/test_music_mac.py`:

```python
import re

import pytest

from music_agent import music

pytestmark = pytest.mark.mac


def test_read_real_library():
    tracks = music.read_library()
    assert len(tracks) > 0
    assert all(re.fullmatch(r"[0-9A-F]{16}", t.persistent_id) for t in tracks[:100])
    assert any(t.played_date is not None for t in tracks)
```

Run: `uv run pytest -m mac tests/test_music_mac.py -v`
Expected: PASS on a Mac with a Music library. The first run may show a macOS Automation prompt; approve it.

- [ ] **Step 7: Confirm the JXA script ships in the wheel**

Run: `uv build && unzip -l dist/music_agent-0.1.0-py3-none-any.whl | grep jxa/`
Expected: the output lists `music_agent/jxa/read_library.js`. If it doesn't, add `[tool.uv.build-backend] source-include = ["src/music_agent/jxa/*.js"]` to `pyproject.toml` and rebuild.

- [ ] **Step 8: Commit**

```bash
git add src/music_agent/music.py src/music_agent/jxa tests/test_music.py tests/test_music_mac.py
git commit -m "feat: read library tracks from Music.app via JXA"
```

---

### Task 8: Creating playlists in Music.app

**Files:**
- Create: `src/music_agent/jxa/create_playlist.js`, `tests/jxa/delete_playlist.js`
- Modify: `src/music_agent/music.py` (append)
- Test: `tests/test_music.py` (append), `tests/test_music_mac.py` (append)

**Interfaces:**
- Consumes: `run_jxa`, `Runner`, `MusicError` (Task 7).
- Produces: `@dataclass(frozen=True) PlaylistResult(name: str, persistent_id: str, track_count: int, missing_ids: list[str])`; `create_playlist(name: str, track_ids: list[str], folder: str, fallback_name: str, description: str = "", runner: Runner | None = None) -> PlaylistResult`. It raises `ValueError` for an empty name or an empty track list. Duplicate IDs are removed, keeping order.

Behavior verified on the author's Mac, 2026-09-27:
- `Music.make({new: 'folderPlaylist'})` creates a folder.
- `Music.make({new: 'playlist', at: folder})` nests a playlist inside it.
- `Music.playlists.whose({name})` finds both. `Music.folderPlaylists.whose` is **not** a function.

- [ ] **Step 1: Write the JXA script**

`src/music_agent/jxa/create_playlist.js`:

```javascript
// argv[0]: JSON {name, fallback_name, folder, description, track_ids}
function run(argv) {
  const req = JSON.parse(argv[0]);
  const Music = Application('Music');
  const lib = Music.libraryPlaylists[0];
  const exists = (n) => Music.playlists.whose({name: n}).length > 0;

  let name = req.name;
  if (exists(name)) {
    name = req.fallback_name;
    let i = 2;
    while (exists(name)) { name = `${req.fallback_name} ${i}`; i += 1; }
  }

  let folder = null;
  const candidates = Music.playlists.whose({name: req.folder});
  for (let i = 0; i < candidates.length; i += 1) {
    if (candidates[i].class() === 'folderPlaylist') { folder = candidates[i]; break; }
  }
  if (!folder) folder = Music.make({new: 'folderPlaylist', withProperties: {name: req.folder}});

  const playlist = Music.make({new: 'playlist', at: folder, withProperties: {name: name}});
  const missing = [];
  try {
    if (req.description) {
      try { playlist.description = req.description; } catch (e) { /* not supported: ignore */ }
    }
    for (const id of req.track_ids) {
      const hits = lib.tracks.whose({persistentID: id});
      if (hits.length === 0) { missing.push(id); continue; }
      Music.duplicate(hits[0], {to: playlist});
    }
  } catch (e) {
    Music.delete(playlist); // never leave a half-built playlist behind
    throw e;
  }
  return JSON.stringify({
    name: name,
    persistent_id: playlist.persistentID(),
    track_count: playlist.tracks.length,
    missing_ids: missing,
  });
}
```

`tests/jxa/delete_playlist.js` (test-only cleanup):

```javascript
// argv: names of playlists/folders to delete. Prints how many were deleted.
function run(argv) {
  const Music = Application('Music');
  let deleted = 0;
  for (const name of argv) {
    const hits = Music.playlists.whose({name: name});
    for (let i = hits.length - 1; i >= 0; i -= 1) { Music.delete(hits[i]); deleted += 1; }
  }
  return String(deleted);
}
```

- [ ] **Step 2: Write the failing unit tests (append to `tests/test_music.py`)**

```python
def test_create_playlist_builds_payload_and_parses_result():
    calls = []
    out = json.dumps(
        {"name": "Rock (Sep 27)", "persistent_id": "ABCDEF0123456789", "track_count": 2, "missing_ids": ["GONE"]}
    )
    result = music.create_playlist(
        name="Rock",
        track_ids=["A1", "B2", "A1", "GONE"],
        folder="Music Agent",
        fallback_name="Rock (Sep 27)",
        description="rock from this week",
        runner=fake_runner(stdout=out, calls=calls),
    )
    args = calls[0]
    assert args[3].endswith("jxa/create_playlist.js")
    payload = json.loads(args[4])
    assert payload == {
        "name": "Rock",
        "fallback_name": "Rock (Sep 27)",
        "folder": "Music Agent",
        "description": "rock from this week",
        "track_ids": ["A1", "B2", "GONE"],
    }
    assert result == music.PlaylistResult("Rock (Sep 27)", "ABCDEF0123456789", 2, ["GONE"])


@pytest.mark.parametrize(("name", "ids"), [("  ", ["A"]), ("Rock", [])])
def test_create_playlist_rejects_empty_input(name, ids):
    with pytest.raises(ValueError):
        music.create_playlist(name, ids, "Music Agent", "x", runner=fake_runner(stdout="{}"))
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_music.py -v`
Expected: the two new tests FAIL with `AttributeError: ... has no attribute 'create_playlist'`.

- [ ] **Step 4: Implement (append to `src/music_agent/music.py`)**

Add `from dataclasses import dataclass` to the imports, then append:

```python
@dataclass(frozen=True)
class PlaylistResult:
    name: str
    persistent_id: str
    track_count: int
    missing_ids: list[str]


def create_playlist(
    name: str,
    track_ids: list[str],
    folder: str,
    fallback_name: str,
    description: str = "",
    runner: Runner | None = None,
) -> PlaylistResult:
    ids = list(dict.fromkeys(track_ids))
    if not name.strip():
        raise ValueError("Playlist name is empty.")
    if not ids:
        raise ValueError("No tracks to add to the playlist.")
    payload = json.dumps(
        {
            "name": name.strip(),
            "fallback_name": fallback_name,
            "folder": folder,
            "description": description,
            "track_ids": ids,
        },
        ensure_ascii=False,
    )
    data = json.loads(run_jxa("create_playlist.js", payload, runner=runner))
    return PlaylistResult(
        name=data["name"],
        persistent_id=data["persistent_id"],
        track_count=int(data["track_count"]),
        missing_ids=list(data["missing_ids"]),
    )
```

- [ ] **Step 5: Run unit tests**

Run: `uv run pytest tests/test_music.py -v`
Expected: all pass.

- [ ] **Step 6: Add the Mac integration test (append to `tests/test_music_mac.py`)**

```python
import subprocess
from pathlib import Path

CLEANUP = Path(__file__).parent / "jxa" / "delete_playlist.js"
TEST_FOLDER = "zz music-agent test folder"
TEST_NAME = "zz music-agent test playlist"


def _cleanup():
    subprocess.run(
        ["osascript", "-l", "JavaScript", str(CLEANUP), TEST_NAME, f"{TEST_NAME} (fallback)", TEST_FOLDER],
        capture_output=True, text=True, check=False,
    )


def test_create_playlist_in_folder_and_report_missing():
    played = sorted(
        (t for t in music.read_library() if t.played_date is not None),
        key=lambda t: t.played_date,
        reverse=True,
    )[:3]
    ids = [t.persistent_id for t in played] + ["FFFFFFFFFFFFFFFF"]
    try:
        first = music.create_playlist(TEST_NAME, ids, TEST_FOLDER, f"{TEST_NAME} (fallback)")
        assert first.name == TEST_NAME
        assert first.track_count == 3
        assert first.missing_ids == ["FFFFFFFFFFFFFFFF"]
        second = music.create_playlist(TEST_NAME, ids[:1], TEST_FOLDER, f"{TEST_NAME} (fallback)")
        assert second.name == f"{TEST_NAME} (fallback)"
    finally:
        _cleanup()
```

Run: `uv run pytest -m mac tests/test_music_mac.py -v`
Expected: PASS. Afterwards, check in Music.app that no "zz music-agent test" items remain.

- [ ] **Step 7: Commit**

```bash
git add src/music_agent/music.py src/music_agent/jxa/create_playlist.js tests/jxa tests/test_music.py tests/test_music_mac.py
git commit -m "feat: create playlists in a Music.app folder via JXA"
```

---

### Task 9: Queries

**Files:**
- Create: `src/music_agent/queries.py`
- Test: `tests/test_queries.py`

**Interfaces:**
- Consumes: `Store`, `to_iso`, `from_iso`, `get_meta` (Task 4); `Period` (Task 3); `genres.matches`, `genres.family_of` (Task 2).
- Produces:
  - `@dataclass(frozen=True) TrackRow(id, name, artist, album, genre, plays_in_range: int, last_played: datetime | None, approx: bool)` with `.as_dict(tz) -> dict`
  - `local_iso(dt: datetime | None, tz: ZoneInfo) -> str | None`
  - `install_time(store) -> datetime | None`
  - `played_tracks(store, period, family=None, genres=None, limit=None) -> list[TrackRow]`
  - `listening_stats(store, period, tz, family=None, genres=None) -> dict`
  - `all_time_top(store, by: str, limit: int = 10) -> list[dict]`
  - `list_genres(store, period: Period | None = None) -> list[dict]`
  - `search_library(store, query=None, artist=None, genre=None, limit=50) -> list[dict]`
  - `track_names(store, ids: list[str]) -> dict[str, str]`
  - `status_summary(store, now: datetime) -> dict`

How the numbers are counted:
- A track has been played in a window if it has a `plays` row in the window **or** its `played_date` falls in the window.
- `plays_in_range = max(play rows in the window, 1 if played_date is in the window else 0)`.
- `plays_is_lower_bound` is true when the window starts before `install_at`, or when there's no install time yet.

- [ ] **Step 1: Write the failing tests**

`tests/test_queries.py`:

```python
from zoneinfo import ZoneInfo

import pytest

from music_agent import queries as q
from music_agent import store as st
from music_agent.models import PlayEvent
from music_agent.periods import resolve
from tests.factories import dt, make_track

LA = ZoneInfo("America/Los_Angeles")
NOW = dt("2026-09-27T19:00:00")  # 12:00 PDT Sunday


@pytest.fixture
def store(isolated_home):
    s = st.Store(isolated_home / "plays.db")
    tracks = [
        make_track("A", name="Heretic", artist="Avenged Sevenfold", genre="Hard Rock",
                   played_count=10, played_date=dt("2026-09-27T18:00:00")),
        make_track("B", name="Zero", artist="The Smashing Pumpkins", genre="Alternative",
                   played_count=4, played_date=dt("2026-09-26T20:00:00")),
        make_track("C", name="Money Trees", artist="Kendrick Lamar", genre="Hip-Hop/Rap",
                   played_count=7, played_date=dt("2026-09-27T17:00:00")),
        make_track("D", name="Young Lust", artist="Pink Floyd", genre="Rock",
                   played_count=2, played_date=dt("2026-03-01T00:00:00")),
        make_track("E", name="Unplayed 100% Pure", artist="Nobody", genre="Jazz"),
        make_track("F", name="Gone", artist="Removed Band", genre="Rock",
                   played_count=50, played_date=dt("2026-01-01T00:00:00")),
    ]
    plays = [
        PlayEvent("A", dt("2026-09-27T18:00:00"), None, dt("2026-09-27T18:05:00"), False),
        PlayEvent("A", dt("2026-09-27T18:00:00"), dt("2026-09-27T16:00:00"), dt("2026-09-27T18:05:00"), True),
        PlayEvent("A", dt("2026-09-27T18:00:00"), dt("2026-09-27T16:00:00"), dt("2026-09-27T18:05:00"), True),
        PlayEvent("C", dt("2026-09-27T17:00:00"), None, dt("2026-09-27T17:02:00"), False),
        PlayEvent("B", dt("2026-09-26T20:00:00"), None, dt("2026-09-26T20:10:00"), False),
    ]
    with s.transaction() as conn:
        st.upsert_tracks(conn, tracks, dt("2026-09-27T18:05:00"))
        st.mark_removed(conn, ["F"], dt("2026-09-27T18:05:00"))
        st.insert_plays(conn, plays)
        st.set_meta(conn, "install_at", "2026-09-20T00:00:00+00:00")
        st.record_snapshot(conn, dt("2026-09-27T18:05:00"), 5, 3, 600)
    return s


def period(name):
    return resolve(name, None, None, LA, NOW)


def test_played_tracks_today_counts_and_order(store):
    rows = q.played_tracks(store, period("today"))
    assert [(r.id, r.plays_in_range) for r in rows] == [("A", 3), ("C", 1)]
    assert rows[0].approx is True and rows[1].approx is False


def test_played_tracks_genre_family(store):
    rows = q.played_tracks(store, period("past_week"), family="rock")
    assert [r.id for r in rows] == ["A", "B"]


def test_played_tracks_uses_played_date_before_install(store):
    rows = q.played_tracks(store, period("this_year"))
    assert {r.id: r.plays_in_range for r in rows} == {"A": 3, "C": 1, "B": 1, "D": 1, "F": 1}


def test_played_tracks_limit(store):
    assert len(q.played_tracks(store, period("this_year"), limit=2)) == 2


def test_listening_stats_today(store):
    stats = q.listening_stats(store, period("today"), LA)
    assert stats["plays"] == 4
    assert stats["distinct_tracks"] == 2
    assert stats["distinct_artists"] == 2
    assert stats["plays_is_lower_bound"] is False
    assert stats["has_approx_times"] is True
    assert stats["top_tracks"][0] == {"name": "Heretic", "artist": "Avenged Sevenfold", "plays": 3}
    assert stats["top_genres"][0] == {"genre": "Hard Rock", "plays": 3, "tracks": 1}
    assert stats["period"]["start"] == "2026-09-27T00:00-07:00"
    assert stats["plays_counted_since"] == "2026-09-19T17:00-07:00"


def test_listening_stats_this_year_is_lower_bound(store):
    stats = q.listening_stats(store, period("this_year"), LA)
    assert stats["plays_is_lower_bound"] is True
    assert stats["distinct_tracks"] == 5


def test_listening_stats_empty_period(store):
    stats = q.listening_stats(store, resolve(None, "2020-01-01", "2020-01-31", LA, NOW), LA)
    assert stats["plays"] == 0 and stats["distinct_tracks"] == 0 and stats["top_tracks"] == []


def test_all_time_top(store):
    assert [r["name"] for r in q.all_time_top(store, "track", 2)] == ["Heretic", "Money Trees"]
    assert q.all_time_top(store, "artist", 1) == [{"artist": "Avenged Sevenfold", "plays": 10, "tracks": 1}]
    assert q.all_time_top(store, "genre", 1)[0]["genre"] == "Hard Rock"
    with pytest.raises(ValueError):
        q.all_time_top(store, "album")


def test_removed_tracks_excluded_from_all_time(store):
    assert "Gone" not in [r["name"] for r in q.all_time_top(store, "track", 10)]


def test_list_genres_library_and_period(store):
    library = {g["genre"]: g for g in q.list_genres(store)}
    assert library["Hard Rock"] == {"genre": "Hard Rock", "family": "rock", "tracks": 1, "plays": 10}
    assert "Jazz" in library
    week = {g["genre"] for g in q.list_genres(store, period("past_week"))}
    assert week == {"Hard Rock", "Alternative", "Hip-Hop/Rap"}


def test_search_library(store):
    assert [r["id"] for r in q.search_library(store, query="money")] == ["C"]
    assert [r["id"] for r in q.search_library(store, artist="pink")] == ["D"]
    assert [r["id"] for r in q.search_library(store, genre="rock")] == ["A", "B", "D"]


def test_search_library_treats_wildcards_literally(store):
    assert [r["id"] for r in q.search_library(store, query="100%")] == ["E"]
    assert [r["id"] for r in q.search_library(store, query="%")] == ["E"]  # literal %, not "match all"
    assert q.search_library(store, query="_") == []
    assert q.search_library(store, query="O'Brien") == []


def test_track_names(store):
    assert q.track_names(store, ["A", "C", "missing"]) == {
        "A": "Avenged Sevenfold - Heretic",
        "C": "Kendrick Lamar - Money Trees",
    }


def test_status_summary(store):
    summary = q.status_summary(store, NOW)
    assert summary["track_count"] == 5
    assert summary["plays_recorded"] == 5
    assert summary["install_at"] == dt("2026-09-20T00:00:00")
    assert summary["last_snapshot"]["events_added"] == 3
    assert summary["last_snapshot"]["error"] is None
    assert summary["sync_lag"]["samples"] == 3
    assert summary["sync_lag"]["median_min"] == 5.0
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_queries.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`src/music_agent/queries.py`:

```python
"""Read-only questions over the store: stats, track lists, genres, search, status."""

from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import genres as genre_mod
from . import store as st
from .periods import Period


@dataclass(frozen=True)
class TrackRow:
    id: str
    name: str
    artist: str
    album: str
    genre: str
    plays_in_range: int
    last_played: datetime | None
    approx: bool

    def as_dict(self, tz: ZoneInfo) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "artist": self.artist,
            "album": self.album,
            "genre": self.genre,
            "plays_in_range": self.plays_in_range,
            "last_played": local_iso(self.last_played, tz),
        }


def local_iso(dt: datetime | None, tz: ZoneInfo) -> str | None:
    return None if dt is None else dt.astimezone(tz).isoformat(timespec="minutes")


def install_time(store: st.Store) -> datetime | None:
    with store.connect() as conn:
        return st.from_iso(st.get_meta(conn, "install_at"))


_PLAYED_SQL = """
WITH p AS (
    SELECT persistent_id, COUNT(*) AS n, MAX(approx) AS any_approx
    FROM plays WHERE played_at >= :s AND played_at < :e
    GROUP BY persistent_id
),
ids AS (
    SELECT persistent_id FROM p
    UNION
    SELECT persistent_id FROM tracks WHERE played_date >= :s AND played_date < :e
)
SELECT t.persistent_id, t.name, t.artist, t.album, t.genre, t.played_date,
       COALESCE(p.n, 0) AS n, COALESCE(p.any_approx, 0) AS any_approx
FROM ids
JOIN tracks t ON t.persistent_id = ids.persistent_id
LEFT JOIN p ON p.persistent_id = ids.persistent_id
"""


def played_tracks(
    store: st.Store,
    period: Period,
    family: str | None = None,
    genres: list[str] | None = None,
    limit: int | None = None,
) -> list[TrackRow]:
    params = {"s": st.to_iso(period.start), "e": st.to_iso(period.end)}
    with store.connect() as conn:
        rows = conn.execute(_PLAYED_SQL, params).fetchall()
    out: list[TrackRow] = []
    for r in rows:
        if not genre_mod.matches(r["genre"], family, genres):
            continue
        played_date = st.from_iso(r["played_date"])
        in_range = played_date is not None and period.start <= played_date < period.end
        out.append(
            TrackRow(
                id=r["persistent_id"],
                name=r["name"],
                artist=r["artist"],
                album=r["album"],
                genre=r["genre"],
                plays_in_range=max(r["n"], 1 if in_range else 0),
                last_played=played_date,
                approx=bool(r["any_approx"]),
            )
        )
    out.sort(
        key=lambda t: (
            -t.plays_in_range,
            -(t.last_played.timestamp() if t.last_played else 0),
            t.artist,
            t.name,
        )
    )
    return out if limit is None else out[:limit]


def listening_stats(
    store: st.Store,
    period: Period,
    tz: ZoneInfo,
    family: str | None = None,
    genres: list[str] | None = None,
) -> dict:
    rows = played_tracks(store, period, family, genres)
    installed = install_time(store)
    artist_plays: Counter[str] = Counter()
    artist_tracks: Counter[str] = Counter()
    genre_plays: Counter[str] = Counter()
    genre_tracks: Counter[str] = Counter()
    for r in rows:
        artist_plays[r.artist] += r.plays_in_range
        artist_tracks[r.artist] += 1
        genre_plays[r.genre] += r.plays_in_range
        genre_tracks[r.genre] += 1
    return {
        "period": {
            "label": period.label,
            "start": local_iso(period.start, tz),
            "end": local_iso(period.end, tz),
        },
        "plays": sum(r.plays_in_range for r in rows),
        "plays_is_lower_bound": installed is None or period.start < installed,
        "plays_counted_since": local_iso(installed, tz),
        "distinct_tracks": len(rows),
        "distinct_artists": len(artist_tracks),
        "top_tracks": [{"name": r.name, "artist": r.artist, "plays": r.plays_in_range} for r in rows[:5]],
        "top_artists": [
            {"artist": a, "plays": n, "tracks": artist_tracks[a]} for a, n in artist_plays.most_common(5)
        ],
        "top_genres": [
            {"genre": g, "plays": n, "tracks": genre_tracks[g]} for g, n in genre_plays.most_common(5)
        ],
        "has_approx_times": any(r.approx for r in rows),
    }


def all_time_top(store: st.Store, by: str, limit: int = 10) -> list[dict]:
    with store.connect() as conn:
        if by == "track":
            rows = conn.execute(
                "SELECT name, artist, genre, played_count AS plays FROM tracks "
                "WHERE removed_at IS NULL AND played_count > 0 "
                "ORDER BY played_count DESC, artist, name LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        if by in ("artist", "genre"):
            rows = conn.execute(
                f"SELECT {by}, SUM(played_count) AS plays, COUNT(*) AS tracks FROM tracks "
                f"WHERE removed_at IS NULL AND played_count > 0 "
                f"GROUP BY {by} ORDER BY plays DESC, {by} LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
    raise ValueError(f"by must be 'track', 'artist' or 'genre', not {by!r}")


def list_genres(store: st.Store, period: Period | None = None) -> list[dict]:
    if period is None:
        with store.connect() as conn:
            rows = conn.execute(
                "SELECT genre, COUNT(*) AS tracks, SUM(played_count) AS plays FROM tracks "
                "WHERE removed_at IS NULL GROUP BY genre ORDER BY plays DESC, genre"
            ).fetchall()
        return [
            {"genre": r["genre"], "family": genre_mod.family_of(r["genre"]), "tracks": r["tracks"], "plays": r["plays"]}
            for r in rows
        ]
    plays: Counter[str] = Counter()
    tracks: Counter[str] = Counter()
    for r in played_tracks(store, period):
        plays[r.genre] += r.plays_in_range
        tracks[r.genre] += 1
    return [
        {"genre": g, "family": genre_mod.family_of(g), "tracks": tracks[g], "plays": n}
        for g, n in plays.most_common()
    ]


def _like(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def search_library(
    store: st.Store,
    query: str | None = None,
    artist: str | None = None,
    genre: str | None = None,
    limit: int = 50,
) -> list[dict]:
    sql = "SELECT * FROM tracks WHERE removed_at IS NULL"
    params: list[str] = []
    if query:
        sql += (
            " AND (name LIKE ? ESCAPE '\\' OR artist LIKE ? ESCAPE '\\' OR album LIKE ? ESCAPE '\\')"
        )
        params += [_like(query)] * 3
    if artist:
        sql += " AND artist LIKE ? ESCAPE '\\'"
        params.append(_like(artist))
    sql += " ORDER BY played_count DESC, artist, name"
    with store.connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    out = []
    for r in rows:
        if genre and not genre_mod.matches(r["genre"], family=genre):
            continue
        out.append(
            {
                "id": r["persistent_id"],
                "name": r["name"],
                "artist": r["artist"],
                "album": r["album"],
                "genre": r["genre"],
                "lifetime_plays": r["played_count"],
            }
        )
        if len(out) >= limit:
            break
    return out


def track_names(store: st.Store, ids: list[str]) -> dict[str, str]:
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    with store.connect() as conn:
        rows = conn.execute(
            f"SELECT persistent_id, artist, name FROM tracks WHERE persistent_id IN ({placeholders})",
            ids,
        ).fetchall()
    return {r["persistent_id"]: f"{r['artist']} - {r['name']}" for r in rows}


def status_summary(store: st.Store, now: datetime) -> dict:
    with store.connect() as conn:
        last = conn.execute("SELECT * FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
        track_count = conn.execute(
            "SELECT COUNT(*) FROM tracks WHERE removed_at IS NULL"
        ).fetchone()[0]
        plays = conn.execute("SELECT COUNT(*) FROM plays").fetchone()[0]
        lag_rows = conn.execute(
            "SELECT played_at, detected_at FROM plays WHERE approx = 0 AND detected_at >= ?",
            (st.to_iso(now - timedelta(days=30)),),
        ).fetchall()
        installed = st.from_iso(st.get_meta(conn, "install_at"))
    lags = sorted(
        max(0.0, (st.from_iso(r["detected_at"]) - st.from_iso(r["played_at"])).total_seconds() / 60)
        for r in lag_rows
    )
    sync_lag = None
    if lags:
        sync_lag = {
            "median_min": round(statistics.median(lags), 1),
            "p95_min": round(lags[min(len(lags) - 1, int(0.95 * len(lags)))], 1),
            "samples": len(lags),
        }
    return {
        "install_at": installed,
        "track_count": track_count,
        "plays_recorded": plays,
        "last_snapshot": None
        if last is None
        else {
            "at": st.from_iso(last["taken_at"]),
            "track_count": last["track_count"],
            "events_added": last["events_added"],
            "error": last["error"],
        },
        "sync_lag": sync_lag,
    }
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_queries.py -v`
Expected: all pass. The lag samples are the three exact plays, with lags of 5, 2 and 10 minutes, so the median is 5.0.

- [ ] **Step 5: Commit**

```bash
git add src/music_agent/queries.py tests/test_queries.py
git commit -m "feat: add listening queries over the store"
```

---

### Task 10: Claude tools

**Files:**
- Create: `src/music_agent/tools.py`
- Test: `tests/test_tools.py`

**Interfaces:**
- Consumes:
  - `Config` (Task 1)
  - `resolve`, `PERIOD_NAMES`, `Period` (Task 3)
  - `Store` (Task 4)
  - `SnapshotResult` (Task 6)
  - `MusicError`, `PlaylistResult` (Tasks 7–8)
  - all of `queries` (Task 9)
- Produces:
  - `@dataclass(frozen=True) ToolSpec(name: str, description: str, input_schema: dict, handler: Callable[[dict], dict])` with `.to_api() -> dict`
  - `@dataclass ToolContext(store: Store, config: Config, clock: Callable[[], datetime], refresh: Callable[[], SnapshotResult | None], create_playlist: Callable[..., PlaylistResult])`. `create_playlist` is called with keywords: `name`, `track_ids`, `folder`, `fallback_name`, `description`.
  - `class Toolbox(ctx: ToolContext)` with `.definitions() -> list[dict]` and `.run(name: str, args: dict) -> dict`. Every result includes `"now"`; failures return `{"error": str, "now": ...}`.
  - The six tool names: `listening_stats`, `played_tracks`, `all_time_top`, `list_genres`, `search_library`, `create_playlist`.

Refresh rule: tools that take a time window call `ctx.refresh()` first when `period.end > now − 24h`. If the refresh fails, the result gets `"warning"` instead of an error.

- [ ] **Step 1: Write the failing tests**

`tests/test_tools.py`:

```python
import pytest

from music_agent import store as st
from music_agent.config import Config
from music_agent.music import MusicError, PlaylistResult
from music_agent.models import PlayEvent
from music_agent.snapshot import SnapshotResult
from music_agent.tools import ToolContext, Toolbox
from tests.factories import dt, make_track

NOW = dt("2026-09-27T19:00:00")
NAMES = {"listening_stats", "played_tracks", "all_time_top", "list_genres", "search_library", "create_playlist"}


@pytest.fixture
def store(isolated_home):
    s = st.Store(isolated_home / "plays.db")
    with s.transaction() as conn:
        st.upsert_tracks(
            conn,
            [
                make_track("A", name="Heretic", artist="Avenged Sevenfold", genre="Hard Rock",
                           played_count=10, played_date=dt("2026-09-27T18:00:00")),
                make_track("C", name="Money Trees", artist="Kendrick Lamar", genre="Hip-Hop/Rap",
                           played_count=7, played_date=dt("2026-09-27T17:00:00")),
            ],
            dt("2026-09-27T18:05:00"),
        )
        st.insert_plays(conn, [PlayEvent("A", dt("2026-09-27T18:00:00"), None, dt("2026-09-27T18:05:00"), False)])
        st.set_meta(conn, "install_at", "2026-09-20T00:00:00+00:00")
    return s


class Recorder:
    def __init__(self, refresh_result=None, refresh_error=None):
        self.refresh_calls = 0
        self.playlist_calls = []
        self.refresh_result = refresh_result
        self.refresh_error = refresh_error

    def refresh(self):
        self.refresh_calls += 1
        if self.refresh_error:
            raise self.refresh_error
        return self.refresh_result

    def create_playlist(self, **kwargs):
        self.playlist_calls.append(kwargs)
        return PlaylistResult(kwargs["name"], "PL1", len(kwargs["track_ids"]) - 1, ["GONE"])


def make_toolbox(store, recorder):
    ctx = ToolContext(
        store=store,
        config=Config(timezone="America/Los_Angeles"),
        clock=lambda: NOW,
        refresh=recorder.refresh,
        create_playlist=recorder.create_playlist,
    )
    return Toolbox(ctx)


def test_definitions_are_valid_schemas(store):
    defs = make_toolbox(store, Recorder()).definitions()
    assert {d["name"] for d in defs} == NAMES
    for d in defs:
        assert d["description"]
        assert d["input_schema"]["type"] == "object"
        assert d["input_schema"]["additionalProperties"] is False
        assert set(d["input_schema"].get("required", [])) <= set(d["input_schema"]["properties"])


def test_listening_stats_today_refreshes_and_adds_now(store):
    rec = Recorder()
    result = make_toolbox(store, rec).run("listening_stats", {"period": "today"})
    assert rec.refresh_calls == 1
    assert result["distinct_tracks"] == 2
    assert result["now"] == "2026-09-27T12:00-07:00"
    assert "warning" not in result


def test_old_explicit_range_does_not_refresh(store):
    rec = Recorder()
    make_toolbox(store, rec).run("listening_stats", {"start": "2026-01-01", "end": "2026-01-31"})
    assert rec.refresh_calls == 0


def test_refresh_failure_becomes_warning(store):
    rec = Recorder(refresh_error=MusicError("Music.app script failed", -1))
    result = make_toolbox(store, rec).run("listening_stats", {"period": "today"})
    assert "Could not refresh" in result["warning"]
    assert result["distinct_tracks"] == 2


def test_refresh_result_with_error_becomes_warning(store):
    failed = SnapshotResult(NOW, 0, 0, False, [], error="boom")
    result = make_toolbox(store, Recorder(refresh_result=failed)).run("played_tracks", {"period": "today"})
    assert "boom" in result["warning"]


def test_played_tracks_shape_and_limit(store):
    result = make_toolbox(store, Recorder()).run("played_tracks", {"period": "today", "limit": 1})
    assert result["count"] == 1 and result["truncated"] is True
    assert result["tracks"][0]["id"] == "A"
    assert result["tracks"][0]["last_played"] == "2026-09-27T11:00-07:00"


def test_played_tracks_genre_family(store):
    result = make_toolbox(store, Recorder()).run("played_tracks", {"period": "today", "genre_family": "rock"})
    assert [t["id"] for t in result["tracks"]] == ["A"]


def test_bad_period_returns_error(store):
    result = make_toolbox(store, Recorder()).run("listening_stats", {"period": "fortnight"})
    assert "Unknown period" in result["error"]
    assert "now" in result


def test_unknown_tool(store):
    assert "Unknown tool" in make_toolbox(store, Recorder()).run("delete_everything", {})["error"]


def test_all_time_top_and_search_and_genres(store):
    box = make_toolbox(store, Recorder())
    assert box.run("all_time_top", {"by": "artist"})["results"][0]["artist"] == "Avenged Sevenfold"
    assert box.run("search_library", {"query": "money"})["results"][0]["id"] == "C"
    genres = box.run("list_genres", {})["genres"]
    assert {g["genre"] for g in genres} == {"Hard Rock", "Hip-Hop/Rap"}


def test_create_playlist_passes_folder_and_fallback_and_reports_missing(store):
    rec = Recorder()
    result = make_toolbox(store, rec).run(
        "create_playlist", {"name": "Rock week", "track_ids": ["A", "GONE"], "description": "rock"}
    )
    call = rec.playlist_calls[0]
    assert call == {
        "name": "Rock week",
        "track_ids": ["A", "GONE"],
        "folder": "Music Agent",
        "fallback_name": "Rock week (2026-09-27)",
        "description": "rock",
    }
    assert result["name"] == "Rock week"
    assert result["track_count"] == 1
    assert result["missing_track_ids"] == ["GONE"]
    assert result["folder"] == "Music Agent"
    assert result["sample_tracks"] == ["Avenged Sevenfold - Heretic"]


def test_unexpected_exception_is_contained(store):
    rec = Recorder()

    def explode(**kwargs):
        raise KeyError("surprise")

    rec.create_playlist = explode
    result = make_toolbox(store, rec).run("create_playlist", {"name": "x", "track_ids": ["A"]})
    assert result["error"].startswith("Internal error in create_playlist")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_tools.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`src/music_agent/tools.py`:

```python
"""The six tools Claude can call. Handlers return JSON-serializable dicts."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from . import periods, queries
from .config import Config
from .music import MusicError, PlaylistResult
from .snapshot import SnapshotResult
from .store import Store

log = logging.getLogger(__name__)

_WINDOW_PROPS = {
    "period": {
        "type": "string",
        "enum": list(periods.PERIOD_NAMES),
        "description": "Named time window. Use this OR start/end, not both.",
    },
    "start": {
        "type": "string",
        "description": "Start date or datetime in ISO 8601, local time, e.g. 2026-09-01.",
    },
    "end": {
        "type": "string",
        "description": "End date (that whole day is included) or datetime, local time. Defaults to now.",
    },
}
_GENRE_PROPS = {
    "genre_family": {
        "type": "string",
        "description": "Broad genre family such as rock, hip-hop, electronic, pop, r&b, jazz. "
        "Use list_genres to see the user's genres and their families.",
    },
    "genres": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Exact genre names to include (combined with genre_family using OR).",
    },
}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict
    handler: Callable[[dict], dict]

    def to_api(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}


@dataclass
class ToolContext:
    store: Store
    config: Config
    clock: Callable[[], datetime]
    refresh: Callable[[], SnapshotResult | None]
    create_playlist: Callable[..., PlaylistResult]


def _schema(properties: dict, required: list[str] | None = None) -> dict:
    schema = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        schema["required"] = required
    return schema


class Toolbox:
    def __init__(self, ctx: ToolContext):
        self.ctx = ctx
        self.specs = {spec.name: spec for spec in self._build()}

    def definitions(self) -> list[dict]:
        return [spec.to_api() for spec in self.specs.values()]

    def run(self, name: str, args: dict) -> dict:
        spec = self.specs.get(name)
        if spec is None:
            result = {"error": f"Unknown tool: {name}"}
        else:
            try:
                result = spec.handler(args or {})
            except (ValueError, MusicError) as exc:
                result = {"error": str(exc)}
            except Exception as exc:  # noqa: BLE001 - report to Claude instead of crashing the chat
                log.exception("tool %s failed", name)
                result = {"error": f"Internal error in {name}: {exc}"}
        result["now"] = queries.local_iso(self.ctx.clock(), self.ctx.config.tz)
        return result

    # --- helpers -------------------------------------------------------------

    def _period(self, args: dict, required: bool = True) -> periods.Period | None:
        if not required and not (args.get("period") or args.get("start")):
            return None
        return periods.resolve(
            args.get("period"), args.get("start"), args.get("end"), self.ctx.config.tz, self.ctx.clock()
        )

    def _refresh_for(self, period: periods.Period | None) -> dict:
        if period is None or period.end <= self.ctx.clock() - timedelta(hours=24):
            return {}
        stale = (
            f"Could not refresh from Music.app; data may be up to "
            f"{self.ctx.config.snapshot_interval_minutes} minutes old"
        )
        try:
            result = self.ctx.refresh()
        except MusicError as exc:
            return {"warning": f"{stale}: {exc}"}
        if result is not None and result.error:
            return {"warning": f"{stale}: {result.error}"}
        return {}

    # --- handlers ------------------------------------------------------------

    def _listening_stats(self, args: dict) -> dict:
        period = self._period(args)
        extra = self._refresh_for(period)
        stats = queries.listening_stats(
            self.ctx.store, period, self.ctx.config.tz, args.get("genre_family"), args.get("genres")
        )
        return {**stats, **extra}

    def _played_tracks(self, args: dict) -> dict:
        period = self._period(args)
        extra = self._refresh_for(period)
        limit = max(1, min(int(args.get("limit", 200)), 500))
        rows = queries.played_tracks(self.ctx.store, period, args.get("genre_family"), args.get("genres"))
        tz = self.ctx.config.tz
        return {
            "period": {"label": period.label, "start": queries.local_iso(period.start, tz),
                       "end": queries.local_iso(period.end, tz)},
            "count": min(len(rows), limit),
            "truncated": len(rows) > limit,
            "tracks": [r.as_dict(tz) for r in rows[:limit]],
            **extra,
        }

    def _all_time_top(self, args: dict) -> dict:
        limit = max(1, min(int(args.get("limit", 10)), 50))
        return {"results": queries.all_time_top(self.ctx.store, args.get("by", "track"), limit)}

    def _list_genres(self, args: dict) -> dict:
        period = self._period(args, required=False)
        extra = self._refresh_for(period)
        return {"genres": queries.list_genres(self.ctx.store, period), **extra}

    def _search_library(self, args: dict) -> dict:
        limit = max(1, min(int(args.get("limit", 50)), 200))
        results = queries.search_library(
            self.ctx.store, args.get("query"), args.get("artist"), args.get("genre"), limit
        )
        return {"results": results}

    def _create_playlist(self, args: dict) -> dict:
        name = str(args.get("name", ""))
        track_ids = [str(t) for t in args.get("track_ids", [])]
        today = self.ctx.clock().astimezone(self.ctx.config.tz).date().isoformat()
        result = self.ctx.create_playlist(
            name=name,
            track_ids=track_ids,
            folder=self.ctx.config.playlist_folder,
            fallback_name=f"{name} ({today})",
            description=str(args.get("description", "")),
        )
        present = [t for t in dict.fromkeys(track_ids) if t not in set(result.missing_ids)]
        names = queries.track_names(self.ctx.store, present[:5])
        return {
            "name": result.name,
            "folder": self.ctx.config.playlist_folder,
            "track_count": result.track_count,
            "missing_track_ids": result.missing_ids,
            "sample_tracks": [names[t] for t in present[:5] if t in names],
        }

    def _build(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                "listening_stats",
                "Listening statistics for a time window: plays, distinct tracks and artists, top "
                "tracks/artists/genres. plays_is_lower_bound=true means repeat plays before "
                "plays_counted_since are not known.",
                _schema({**_WINDOW_PROPS, **_GENRE_PROPS}),
                self._listening_stats,
            ),
            ToolSpec(
                "played_tracks",
                "Tracks played in a time window with their ids (for create_playlist), sorted by "
                "plays in the window.",
                _schema({**_WINDOW_PROPS, **_GENRE_PROPS,
                         "limit": {"type": "integer", "minimum": 1, "maximum": 500}}),
                self._played_tracks,
            ),
            ToolSpec(
                "all_time_top",
                "All-time most played tracks, artists or genres from the library's lifetime play counts.",
                _schema({"by": {"type": "string", "enum": ["track", "artist", "genre"]},
                         "limit": {"type": "integer", "minimum": 1, "maximum": 50}}, ["by"]),
                self._all_time_top,
            ),
            ToolSpec(
                "list_genres",
                "The user's genres with their family, track and play counts; library-wide, or for a "
                "time window if period/start is given.",
                _schema(_WINDOW_PROPS),
                self._list_genres,
            ),
            ToolSpec(
                "search_library",
                "Search the whole library by text (title/artist/album), artist, or genre/genre family. "
                "Returns track ids for create_playlist.",
                _schema({"query": {"type": "string"}, "artist": {"type": "string"},
                         "genre": {"type": "string"},
                         "limit": {"type": "integer", "minimum": 1, "maximum": 200}}),
                self._search_library,
            ),
            ToolSpec(
                "create_playlist",
                "Create a NEW playlist from track ids (from played_tracks or search_library). Never "
                "modifies existing playlists. A date is appended if the name is taken.",
                _schema({"name": {"type": "string"},
                         "track_ids": {"type": "array", "items": {"type": "string"},
                                       "minItems": 1, "maxItems": 500},
                         "description": {"type": "string"}}, ["name", "track_ids"]),
                self._create_playlist,
            ),
        ]
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_tools.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/music_agent/tools.py tests/test_tools.py
git commit -m "feat: add the six claude tools"
```

---

### Task 11: Agent loop, history, usage and budget

**Files:**
- Modify: `src/music_agent/store.py` (append history and usage methods to `Store`)
- Create: `src/music_agent/agent.py`
- Test: `tests/test_agent.py`

**Interfaces:**
- Consumes: `Store`, `to_iso`, `from_iso` (Task 4); `Config` (Task 1); `Toolbox.definitions()` and `Toolbox.run()` (Task 10); `local_iso` (Task 9); `utcnow` (Task 4).
- Produces:
  - `Store` methods:
    - `append_messages(chat_id: str, messages: list[dict], at: datetime) -> None`
    - `load_messages(chat_id: str) -> list[tuple[dict, datetime]]`
    - `clear_messages(chat_id: str) -> None`
    - `record_usage(at: datetime, model: str, input_tokens: int, output_tokens: int, cache_read_tokens: int, cost_usd: float) -> None`
    - `spend_since(since: datetime) -> float`
  - In `agent.py`:
    - `PRICES_PER_MTOK: dict[str, tuple[float, float]]`
    - `cost_usd(model: str, input_tokens: int, output_tokens: int) -> float`
    - `month_start(now: datetime, tz: ZoneInfo) -> datetime`
    - `system_prompt(config: Config, now: datetime) -> str`
    - the message constants `API_ERROR_MSG` and `TOO_MANY_STEPS_MSG`
    - `class Agent(client, store: Store, config: Config, toolbox, clock=utcnow)` with `.respond(chat_id: str, text: str) -> str` and `.reset(chat_id: str) -> None`

Loop rules:
- Check the budget first.
- Load history. After more than 30 minutes idle it resets. It's trimmed to the last 40 messages, starting at a plain user text message.
- Call `client.messages.create(model, max_tokens=4096, system, tools, messages)` at most 10 times per turn.
- Record usage after every call.
- Persist the new messages **only when the turn completes**. On an API error or too many steps, persist nothing.

- [ ] **Step 1: Write the failing tests**

`tests/test_agent.py`:

```python
import json
from datetime import timedelta

import anthropic
import httpx2
import pytest
from anthropic.types import Message

from music_agent import agent as ag
from music_agent import store as st
from music_agent.config import Config
from tests.factories import dt

NOW = dt("2026-09-27T19:00:00")


def msg(content, stop_reason="end_turn", input_tokens=1000, output_tokens=100):
    return Message.model_validate(
        {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": "claude-haiku-4-5",
            "content": content,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        }
    )


def text(t):
    return {"type": "text", "text": t}


def tool_use(tool_id, name, args):
    return {"type": "tool_use", "id": tool_id, "name": name, "input": args}


class FakeMessages:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(json.loads(json.dumps(kwargs, default=str)))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeClient:
    def __init__(self, responses):
        self.messages = FakeMessages(responses)


class StubToolbox:
    def __init__(self, results=None):
        self.results = results or {}
        self.calls = []

    def definitions(self):
        return [{"name": "listening_stats", "description": "d",
                 "input_schema": {"type": "object", "properties": {}}}]

    def run(self, name, args):
        self.calls.append((name, args))
        return dict(self.results.get(name, {"ok": True}))


class Clock:
    def __init__(self, now=NOW):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def store(isolated_home):
    return st.Store(isolated_home / "plays.db")


def make_agent(store, responses, toolbox=None, clock=None, budget=5.0):
    client = FakeClient(responses)
    config = Config(timezone="America/Los_Angeles", monthly_budget_usd=budget)
    agent = ag.Agent(client, store, config, toolbox or StubToolbox(), clock or Clock())
    return agent, client


def test_cost_usd():
    assert ag.cost_usd("claude-haiku-4-5", 1_000_000, 1_000_000) == pytest.approx(6.0)
    assert ag.cost_usd("claude-sonnet-5", 1000, 100) == pytest.approx(0.003)
    assert ag.cost_usd("some-future-model", 1_000_000, 0) == pytest.approx(5.0)


def test_month_start():
    from zoneinfo import ZoneInfo
    assert ag.month_start(NOW, ZoneInfo("America/Los_Angeles")) == dt("2026-09-01T07:00:00")


def test_system_prompt_mentions_time_and_folder():
    prompt = ag.system_prompt(Config(timezone="America/Los_Angeles"), NOW)
    assert "2026-09-27 12:00" in prompt and "Sunday" in prompt
    assert '"Music Agent"' in prompt


def test_plain_answer_persists_history_and_usage(store):
    agent, client = make_agent(store, [msg([text("You played 4 songs today.")])])
    assert agent.respond("cli", "how many songs today?") == "You played 4 songs today."
    call = client.messages.calls[0]
    assert call["model"] == "claude-haiku-4-5" and call["max_tokens"] == 4096
    assert call["messages"] == [{"role": "user", "content": "how many songs today?"}]
    assert [m["role"] for m, _ in store.load_messages("cli")] == ["user", "assistant"]
    assert store.spend_since(dt("2026-09-01T00:00:00")) == pytest.approx(ag.cost_usd("claude-haiku-4-5", 1000, 100))


def test_tool_loop(store):
    toolbox = StubToolbox({"listening_stats": {"distinct_tracks": 4}})
    agent, client = make_agent(
        store,
        [
            msg([text("Checking."), tool_use("toolu_1", "listening_stats", {"period": "today"})], "tool_use"),
            msg([text("4 songs.")]),
        ],
        toolbox,
    )
    assert agent.respond("cli", "songs today?") == "4 songs."
    assert toolbox.calls == [("listening_stats", {"period": "today"})]
    second = client.messages.calls[1]["messages"]
    assert second[1]["content"][1] == {"type": "tool_use", "id": "toolu_1", "name": "listening_stats",
                                       "input": {"period": "today"}}
    result = second[2]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "toolu_1"
    assert json.loads(result["content"]) == {"distinct_tracks": 4}
    assert "is_error" not in result
    assert len(store.load_messages("cli")) == 4


def test_tool_error_is_flagged(store):
    toolbox = StubToolbox({"listening_stats": {"error": "Unknown period"}})
    agent, client = make_agent(
        store,
        [msg([tool_use("toolu_1", "listening_stats", {"period": "x"})], "tool_use"), msg([text("Sorry.")])],
        toolbox,
    )
    agent.respond("cli", "q")
    assert client.messages.calls[1]["messages"][2]["content"][0]["is_error"] is True


def test_follow_up_includes_history(store):
    agent, client = make_agent(store, [msg([text("first")]), msg([text("second")])])
    agent.respond("cli", "one")
    agent.respond("cli", "two")
    roles = [m["role"] for m in client.messages.calls[1]["messages"]]
    assert roles == ["user", "assistant", "user"]


def test_idle_reset_after_30_minutes(store):
    clock = Clock()
    agent, client = make_agent(store, [msg([text("a")]), msg([text("b")])], clock=clock)
    agent.respond("cli", "one")
    clock.now = NOW + timedelta(minutes=31)
    agent.respond("cli", "two")
    assert client.messages.calls[1]["messages"] == [{"role": "user", "content": "two"}]


def test_reset(store):
    agent, client = make_agent(store, [msg([text("a")]), msg([text("b")])])
    agent.respond("cli", "one")
    agent.reset("cli")
    agent.respond("cli", "two")
    assert len(client.messages.calls[1]["messages"]) == 1


def test_chats_are_separate(store):
    agent, client = make_agent(store, [msg([text("a")]), msg([text("b")])])
    agent.respond("cli", "one")
    agent.respond("tg:1", "two")
    assert len(client.messages.calls[1]["messages"]) == 1


def test_history_is_trimmed_to_a_clean_start(store):
    old = []
    for i in range(30):
        old.append({"role": "user", "content": f"q{i}"})
        old.append({"role": "assistant", "content": [{"type": "text", "text": f"a{i}"}]})
    store.append_messages("cli", old, NOW - timedelta(minutes=1))
    agent, client = make_agent(store, [msg([text("ok")])])
    agent.respond("cli", "new")
    sent = client.messages.calls[0]["messages"]
    assert len(sent) <= ag.MAX_HISTORY_MESSAGES + 1
    assert sent[0]["role"] == "user" and isinstance(sent[0]["content"], str)


def test_budget_exceeded_skips_api(store):
    store.record_usage(NOW, "claude-haiku-4-5", 0, 0, 0, 5.01)
    agent, client = make_agent(store, [])
    reply = agent.respond("cli", "q")
    assert "budget" in reply.lower() and "$5.00" in reply
    assert client.messages.calls == []


def test_last_month_spend_does_not_count(store):
    store.record_usage(dt("2026-08-31T12:00:00"), "claude-haiku-4-5", 0, 0, 0, 50.0)
    agent, _ = make_agent(store, [msg([text("fine")])])
    assert agent.respond("cli", "q") == "fine"


def test_api_error_mid_loop_persists_nothing_and_next_turn_works(store):
    error = anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))
    agent, client = make_agent(
        store,
        [
            msg([tool_use("toolu_1", "listening_stats", {"period": "today"})], "tool_use"),
            error,
            msg([text("recovered")]),
        ],
    )
    assert agent.respond("cli", "q1") == ag.API_ERROR_MSG
    assert store.load_messages("cli") == []
    assert agent.respond("cli", "q2") == "recovered"
    assert client.messages.calls[2]["messages"] == [{"role": "user", "content": "q2"}]


def test_too_many_steps(store):
    looping = [msg([tool_use(f"toolu_{i}", "listening_stats", {})], "tool_use") for i in range(ag.MAX_TOOL_ROUNDS)]
    agent, client = make_agent(store, looping)
    assert agent.respond("cli", "q") == ag.TOO_MANY_STEPS_MSG
    assert len(client.messages.calls) == ag.MAX_TOOL_ROUNDS
    assert store.load_messages("cli") == []


def test_max_tokens_marks_cut_off(store):
    agent, _ = make_agent(store, [msg([text("partial")], "max_tokens")])
    assert agent.respond("cli", "q") == "partial (answer cut off)"


def test_refusal_without_text_saves_no_empty_turn(store):
    agent, client = make_agent(store, [msg([], "refusal"), msg([text("ok")])])
    assert agent.respond("cli", "q") == ag.REFUSAL_MSG
    assert [m["role"] for m, _ in store.load_messages("cli")] == ["user"]
    agent.respond("cli", "q2")
    assert all(m.get("content") for m in client.messages.calls[1]["messages"])
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_agent.py -v`
Expected: FAIL with `ImportError: cannot import name 'agent'`. If instead `import httpx2` fails, check `uv run python -c "import anthropic, sys; print(anthropic.__version__)"`; anthropic 1.x depends on `httpx2`.

- [ ] **Step 3: Add history and usage methods to `Store` (append inside the class in `src/music_agent/store.py`)**

Add `import json` to the imports, then add these methods to `class Store`:

```python
    def append_messages(self, chat_id: str, messages: list[dict], at: datetime) -> None:
        with self.transaction() as conn:
            conn.executemany(
                "INSERT INTO conversations (chat_id, role, content_json, created_at) VALUES (?, ?, ?, ?)",
                [(chat_id, m["role"], json.dumps(m, ensure_ascii=False), to_iso(at)) for m in messages],
            )

    def load_messages(self, chat_id: str) -> list[tuple[dict, datetime]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT content_json, created_at FROM conversations WHERE chat_id = ? ORDER BY id",
                (chat_id,),
            ).fetchall()
        return [(json.loads(r["content_json"]), from_iso(r["created_at"])) for r in rows]

    def clear_messages(self, chat_id: str) -> None:
        with self.transaction() as conn:
            conn.execute("DELETE FROM conversations WHERE chat_id = ?", (chat_id,))

    def record_usage(
        self, at: datetime, model: str, input_tokens: int, output_tokens: int,
        cache_read_tokens: int, cost_usd: float,
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO usage (at, model, input_tokens, output_tokens, cache_read_tokens, cost_usd) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (to_iso(at), model, input_tokens, output_tokens, cache_read_tokens, cost_usd),
            )

    def spend_since(self, since: datetime) -> float:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM usage WHERE at >= ?", (to_iso(since),)
            ).fetchone()
        return float(row[0])
```

- [ ] **Step 4: Implement `src/music_agent/agent.py`**

```python
"""The agent loop: Claude + our tools, with per-chat history, usage tracking and a budget."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

import anthropic

from .config import Config
from .models import utcnow
from .store import Store

log = logging.getLogger(__name__)

# (input, output) USD per million tokens. Unknown models are priced conservatively.
PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-opus-5": (5.0, 25.0),
}
FALLBACK_PRICE = (5.0, 25.0)

MAX_TOKENS = 4096
MAX_TOOL_ROUNDS = 10
MAX_HISTORY_MESSAGES = 40
IDLE_RESET = timedelta(minutes=30)

API_ERROR_MSG = "Claude API unavailable, try again shortly."
TOO_MANY_STEPS_MSG = "I couldn't finish that in a reasonable number of steps. Try a narrower question."
REFUSAL_MSG = "Claude declined to answer that one."


class ToolRunner(Protocol):
    def definitions(self) -> list[dict]: ...
    def run(self, name: str, args: dict) -> dict: ...


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = PRICES_PER_MTOK.get(model, FALLBACK_PRICE)
    return input_tokens * price_in / 1_000_000 + output_tokens * price_out / 1_000_000


def month_start(now: datetime, tz: ZoneInfo) -> datetime:
    local = now.astimezone(tz)
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(now.tzinfo)


def system_prompt(config: Config, now: datetime) -> str:
    local = now.astimezone(config.tz)
    return f"""You are music-agent, a concise assistant for the user's Apple Music library on their Mac.
You answer questions about their listening and create playlists using the tools.
Replies are read on a phone or in a terminal: keep them short, plain text, no markdown tables.

Current local time: {local:%A %Y-%m-%d %H:%M} ({config.timezone}).
Time windows: "today" = since local midnight; "past week" / "last 7 days" = past_week (rolling
7x24h); "this week" = since Monday 00:00; "this month"; "this year" = since Jan 1. For other
ranges pass start/end dates.

Rules:
- Always use the tools for numbers, songs and playlists. Never guess or invent them.
- "How many songs" means distinct_tracks unless the user asks about plays or repeats.
- If plays_is_lower_bound is true, say plays are only fully counted since plays_counted_since
  (e.g. "at least 57 plays").
- For genre requests like "rock", use genre_family and mention which genres were included.
- Create playlists right away when asked. Reply with the playlist name, the number of tracks and
  the first few tracks. Playlists are created in the "{config.playlist_folder}" folder.
- Themed playlists: get candidates with played_tracks, then pick the tracks that fit the theme
  from your knowledge of the songs. If fewer than 5 fit, say so and offer to widen the window
  (past month, or the whole library via search_library) instead of padding.
- If a tool returns an error or a warning, tell the user plainly.
- You cannot edit or delete existing playlists or tracks."""


def _block_param(block) -> dict | None:
    if block.type == "text":
        return {"type": "text", "text": block.text}
    if block.type == "tool_use":
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    return None


def _is_plain_user(message: dict) -> bool:
    return message["role"] == "user" and isinstance(message["content"], str)


class Agent:
    def __init__(
        self,
        client: anthropic.Anthropic,
        store: Store,
        config: Config,
        toolbox: ToolRunner,
        clock: Callable[[], datetime] = utcnow,
    ):
        self.client = client
        self.store = store
        self.config = config
        self.toolbox = toolbox
        self.clock = clock

    def reset(self, chat_id: str) -> None:
        self.store.clear_messages(chat_id)

    def _history(self, chat_id: str, now: datetime) -> list[dict]:
        rows = self.store.load_messages(chat_id)
        if not rows:
            return []
        if now - rows[-1][1] > IDLE_RESET:
            self.store.clear_messages(chat_id)
            return []
        messages = [m for m, _ in rows][-MAX_HISTORY_MESSAGES:]
        while messages and not _is_plain_user(messages[0]):
            messages = messages[1:]
        return messages

    def _record_usage(self, response, now: datetime) -> None:
        usage = response.usage
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        self.store.record_usage(
            now, self.config.model, usage.input_tokens, usage.output_tokens, cache_read,
            cost_usd(self.config.model, usage.input_tokens, usage.output_tokens),
        )

    def respond(self, chat_id: str, text: str) -> str:
        now = self.clock()
        spent = self.store.spend_since(month_start(now, self.config.tz))
        if spent >= self.config.monthly_budget_usd:
            return (
                f"Monthly budget of ${self.config.monthly_budget_usd:.2f} reached "
                f"(spent ${spent:.2f}). Raise monthly_budget_usd in config.toml or wait until next month."
            )
        history = self._history(chat_id, now)
        new: list[dict] = [{"role": "user", "content": text}]
        try:
            for _ in range(MAX_TOOL_ROUNDS):
                response = self.client.messages.create(
                    model=self.config.model,
                    max_tokens=MAX_TOKENS,
                    system=system_prompt(self.config, now),
                    tools=self.toolbox.definitions(),
                    messages=history + new,
                )
                self._record_usage(response, now)
                blocks = [b for b in (_block_param(x) for x in response.content) if b is not None]
                if blocks:  # the API rejects assistant turns with empty content
                    new.append({"role": "assistant", "content": blocks})
                if response.stop_reason != "tool_use":
                    self.store.append_messages(chat_id, new, now)
                    return self._final_text(response)
                results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue
                    output = self.toolbox.run(block.name, block.input)
                    result = {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(output, ensure_ascii=False, default=str),
                    }
                    if "error" in output:
                        result["is_error"] = True
                    results.append(result)
                new.append({"role": "user", "content": results})
            return TOO_MANY_STEPS_MSG
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
            request_id = None
            if isinstance(exc, anthropic.APIStatusError):
                request_id = exc.response.headers.get("request-id")
            log.warning("Claude API error (request-id %s): %s", request_id, exc)
            return API_ERROR_MSG

    @staticmethod
    def _final_text(response) -> str:
        text = "\n".join(b.text for b in response.content if b.type == "text").strip()
        if response.stop_reason == "refusal" and not text:
            return REFUSAL_MSG
        if response.stop_reason == "max_tokens":
            return f"{text} (answer cut off)"
        return text or "(no answer)"
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_agent.py tests/test_store.py -v`
Expected: all pass.
- If `Message.model_validate` rejects the fake payload because of a missing field, add the field the error names (e.g. `"stop_details": None`) to `msg()`.

- [ ] **Step 6: Commit**

```bash
git add src/music_agent/agent.py src/music_agent/store.py tests/test_agent.py
git commit -m "feat: add agent loop with history, usage and budget"
```

---

### Task 12: CLI (`chat`, `ask`, `snapshot`, `status`)

**Files:**
- Create: `src/music_agent/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes:
  - `load_config`, `db_path`, `log_dir`, `get_secret`, `ConfigError` (Task 1)
  - `Store` (Task 4)
  - `run_snapshot`, `maybe_snapshot` (Task 6)
  - `music.read_library`, `music.create_playlist` (Tasks 7–8)
  - `queries.status_summary`, `queries.local_iso` (Task 9)
  - `ToolContext`, `Toolbox` (Task 10)
  - `Agent`, `month_start` (Task 11)
- Produces:
  - `main(argv: list[str] | None = None) -> int`
  - `build_agent(config, store, api_key) -> Agent`
  - `format_status(summary: dict, spent: float, config: Config) -> str`
  - `setup_logging(verbose: bool = False) -> None`
  - the constants `NOT_MAC_MSG` and `NO_KEY_MSG`

- [ ] **Step 1: Write the failing tests**

`tests/test_cli.py`:

```python
import pytest

from music_agent import cli, music
from music_agent import store as st
from music_agent.config import set_secret
from tests.factories import dt, make_track


class StubAgent:
    def __init__(self):
        self.calls = []

    def respond(self, chat_id, text):
        self.calls.append((chat_id, text))
        return f"answer to {text}"

    def reset(self, chat_id):
        self.calls.append((chat_id, "<reset>"))


@pytest.fixture
def stub_agent(monkeypatch):
    agent = StubAgent()
    monkeypatch.setattr(cli, "build_agent", lambda config, store, api_key: agent)
    return agent


def test_not_macos(monkeypatch, capsys):
    monkeypatch.setattr(cli.sys, "platform", "linux")
    assert cli.main(["status"]) == 2
    assert "macOS" in capsys.readouterr().err


def test_snapshot_command(monkeypatch, capsys):
    monkeypatch.setattr(music, "read_library", lambda: [make_track("A", played_count=1)])
    assert cli.main(["snapshot"]) == 0
    out = capsys.readouterr().out
    assert "1 tracks" in out and "baseline" in out


def test_snapshot_failure_exit_code(monkeypatch, capsys):
    def broken():
        raise music.MusicError("no access", -1743)

    monkeypatch.setattr(music, "read_library", broken)
    assert cli.main(["snapshot"]) == 1
    assert "no access" in capsys.readouterr().err


def test_ask_without_key(capsys):
    assert cli.main(["ask", "hi"]) == 2
    assert "keyring set music-agent anthropic_api_key" in capsys.readouterr().err


def test_ask_with_key(stub_agent, capsys):
    set_secret("anthropic_api_key", "sk-test")
    assert cli.main(["ask", "how", "many", "songs?"]) == 0
    assert capsys.readouterr().out.strip() == "answer to how many songs?"
    assert stub_agent.calls == [("cli", "how many songs?")]


def test_chat_loop(stub_agent, monkeypatch, capsys):
    set_secret("anthropic_api_key", "sk-test")
    inputs = iter(["hello", "", "/new", "/exit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    assert cli.main(["chat"]) == 0
    assert stub_agent.calls == [("cli", "hello"), ("cli", "<reset>")]
    assert "answer to hello" in capsys.readouterr().out


def test_chat_ctrl_d_exits(stub_agent, monkeypatch):
    set_secret("anthropic_api_key", "sk-test")

    def eof(prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    assert cli.main(["chat"]) == 0


def test_status_before_any_snapshot(capsys):
    assert cli.main(["status"]) == 0
    assert "No snapshots yet" in capsys.readouterr().out


def test_format_status():
    from music_agent.config import Config

    summary = {
        "install_at": dt("2026-09-20T00:00:00"),
        "track_count": 31699,
        "plays_recorded": 14,
        "last_snapshot": {"at": dt("2026-09-27T19:00:00"), "track_count": 31699, "events_added": 2, "error": None},
        "sync_lag": {"median_min": 0.9, "p95_min": 3.2, "samples": 12},
    }
    text = cli.format_status(summary, 0.12, Config(timezone="America/Los_Angeles"))
    assert "31,699" in text
    assert "2026-09-27 12:00" in text and "+2 plays" in text
    assert "median 0.9 min" in text and "p95 3.2 min" in text
    assert "$0.12 of $5.00" in text


def test_invalid_config_is_reported(isolated_home, capsys):
    isolated_home.mkdir(parents=True, exist_ok=True)
    (isolated_home / "config.toml").write_text('timezone = "Nope/Nope"\n')
    assert cli.main(["status"]) == 2
    assert "Nope/Nope" in capsys.readouterr().err
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement**

`src/music_agent/cli.py`:

```python
"""`music-agent` command-line entry point."""

from __future__ import annotations

import argparse
import logging
import sys

import anthropic

from . import music, queries
from .agent import Agent, month_start
from .config import Config, ConfigError, db_path, get_secret, load_config, log_dir
from .models import utcnow
from .snapshot import maybe_snapshot, run_snapshot
from .store import Store
from .tools import ToolContext, Toolbox

NOT_MAC_MSG = "music-agent only works on macOS (it reads your library from Music.app)."
NO_KEY_MSG = (
    "No Anthropic API key found. Set one with:\n"
    "  uv run keyring set music-agent anthropic_api_key\n"
    "or export ANTHROPIC_API_KEY. (`music-agent setup` will do this for you in a later version.)"
)
CLI_CHAT_ID = "cli"


def setup_logging(verbose: bool = False) -> None:
    log_dir().mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    file_handler = logging.FileHandler(log_dir() / "music-agent.log")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.INFO if verbose else logging.WARNING)
    console.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    root.handlers[:] = [file_handler, console]


def build_agent(config: Config, store: Store, api_key: str) -> Agent:
    ctx = ToolContext(
        store=store,
        config=config,
        clock=utcnow,
        refresh=lambda: maybe_snapshot(store, music.read_library),
        create_playlist=lambda **kwargs: music.create_playlist(**kwargs),
    )
    return Agent(anthropic.Anthropic(api_key=api_key), store, config, Toolbox(ctx))


def format_status(summary: dict, spent: float, config: Config) -> str:
    tz = config.tz

    def when(dt):
        return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M")

    last = summary["last_snapshot"]
    if last is None:
        return "No snapshots yet. Run `music-agent snapshot` to record a baseline."
    lines = [f"Tracks in library: {summary['track_count']:,}"]
    if summary["install_at"]:
        lines.append(f"Recording plays since: {when(summary['install_at'])}")
    if last["error"]:
        lines.append(f"Last snapshot: {when(last['at'])} FAILED: {last['error']}")
    else:
        lines.append(f"Last snapshot: {when(last['at'])} (+{last['events_added']} plays)")
    lines.append(f"Plays recorded: {summary['plays_recorded']:,}")
    lag = summary["sync_lag"]
    if lag:
        lines.append(
            f"Sync lag (incl. polling): median {lag['median_min']} min, p95 {lag['p95_min']} min "
            f"({lag['samples']} plays, last 30 days)"
        )
    lines.append(f"API spend this month: ${spent:.2f} of ${config.monthly_budget_usd:.2f}")
    return "\n".join(lines)


def _status_text(config: Config, store: Store) -> str:
    now = utcnow()
    spent = store.spend_since(month_start(now, config.tz))
    return format_status(queries.status_summary(store, now), spent, config)


def _cmd_snapshot(store: Store) -> int:
    result = run_snapshot(store, music.read_library)
    if result.error:
        print(f"Snapshot failed: {result.error}", file=sys.stderr)
        return 1
    if result.first_run:
        print(f"Recorded a baseline of {result.track_count:,} tracks. Plays are counted from now on.")
    else:
        print(f"Snapshot of {result.track_count:,} tracks: {result.events_added} new plays.")
    return 0


def _require_key() -> str | None:
    key = get_secret("anthropic_api_key")
    if not key:
        print(NO_KEY_MSG, file=sys.stderr)
    return key


def _cmd_chat(agent: Agent) -> int:
    print("music-agent chat. Ask about your listening. /new starts over, /exit quits.")
    while True:
        try:
            line = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line in ("/exit", "/quit"):
            return 0
        if line == "/new":
            agent.reset(CLI_CHAT_ID)
            print("Started a new conversation.")
            continue
        print(agent.respond(CLI_CHAT_ID, line))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="music-agent",
        description="Ask questions about your Apple Music listening and build playlists (macOS).",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="show info logs on stderr")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("chat", help="interactive conversation in the terminal")
    ask = sub.add_parser("ask", help="ask one question")
    ask.add_argument("question", nargs="+")
    sub.add_parser("snapshot", help="record plays from Music.app now")
    sub.add_parser("status", help="show recording status and spend")
    run = sub.add_parser("run", help="run the Telegram bot in the foreground")
    run.add_argument("--no-scheduler", action="store_true", help="don't take snapshots in this process")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if sys.platform != "darwin":
        print(NOT_MAC_MSG, file=sys.stderr)
        return 2
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2
    setup_logging(args.verbose)
    store = Store(db_path())

    if args.command == "snapshot":
        return _cmd_snapshot(store)
    if args.command == "status":
        print(_status_text(config, store))
        return 0
    if args.command in ("chat", "ask"):
        key = _require_key()
        if not key:
            return 2
        agent = build_agent(config, store, key)
        if args.command == "ask":
            print(agent.respond(CLI_CHAT_ID, " ".join(args.question)))
            return 0
        return _cmd_chat(agent)
    if args.command == "run":
        from .bot import run_bot  # imported lazily: only `run` needs python-telegram-bot

        return run_bot(config, store, no_scheduler=args.no_scheduler)
    return 2
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_cli.py -v`
Expected: all pass. The `run` branch isn't exercised yet; Task 13 adds `bot.run_bot`.

- [ ] **Step 5: Try it for real**

Run: `uv run music-agent snapshot && uv run music-agent status`
Expected: "Recorded a baseline of N tracks…", followed by the status block. (This uses your real data folder, because `MUSIC_AGENT_HOME` isn't set outside tests.)

- [ ] **Step 6: Commit**

```bash
git add src/music_agent/cli.py tests/test_cli.py
git commit -m "feat: add chat, ask, snapshot and status commands"
```

---

### Task 13: Telegram bot and `run`

**Files:**
- Create: `src/music_agent/bot.py`
- Test: `tests/test_bot.py`

**Interfaces:**
- Consumes:
  - `Agent.respond` and `Agent.reset` (Task 11)
  - `Config`, `get_secret` (Task 1)
  - `Store` (Task 4)
  - `run_snapshot` (Task 6)
  - `music.read_library` (Task 7)
  - `build_agent`, `_status_text`, `_require_key` (Task 12)
- Produces:
  - `TELEGRAM_LIMIT = 4096`
  - `split_message(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]`
  - `class BotLogic(agent, allowed_user_id: int | None, status_text: Callable[[], str])` with `.is_allowed(user_id: int) -> bool` and `.handle(user_id: int, chat_id: int, text: str) -> list[str] | None`
  - `build_application(token: str, logic: BotLogic) -> telegram.ext.Application`
  - `class SnapshotScheduler(interval_seconds: float, job: Callable[[], object])` with `.start()` and `.stop()`
  - `run_bot(config: Config, store: Store, no_scheduler: bool = False) -> int`

Behavior:
- Chat IDs passed to the agent are `f"tg:{chat_id}"`.
- If `telegram_allowed_user_id` isn't configured, the bot answers every sender with their user ID and how to allow it, and never calls the agent. This is Phase 1's pairing aid.
- Messages from users who aren't allowed are logged and ignored.

- [ ] **Step 1: Write the failing tests**

`tests/test_bot.py`:

```python
import threading
import time

import pytest

from music_agent import bot
from music_agent.config import Config


class StubAgent:
    def __init__(self, reply="ok"):
        self.reply = reply
        self.calls = []

    def respond(self, chat_id, text):
        self.calls.append(("respond", chat_id, text))
        return self.reply

    def reset(self, chat_id):
        self.calls.append(("reset", chat_id))


def logic(agent=None, allowed=42):
    return bot.BotLogic(agent or StubAgent(), allowed, lambda: "status text")


def test_split_short_message():
    assert bot.split_message("hi") == ["hi"]


def test_split_prefers_newlines():
    text = "a" * 3000 + "\n" + "b" * 3000
    parts = bot.split_message(text)
    assert parts == ["a" * 3000, "b" * 3000]


def test_split_hard_when_no_newline():
    parts = bot.split_message("x" * 9000)
    assert [len(p) for p in parts] == [4096, 4096, 808]
    assert all(len(p) <= bot.TELEGRAM_LIMIT for p in parts)


def test_disallowed_user_is_ignored():
    agent = StubAgent()
    assert logic(agent).handle(user_id=7, chat_id=7, text="hi") is None
    assert agent.calls == []


def test_allowed_user_gets_agent_reply():
    agent = StubAgent("4 songs")
    assert logic(agent).handle(42, 100, "how many songs today?") == ["4 songs"]
    assert agent.calls == [("respond", "tg:100", "how many songs today?")]


def test_long_reply_is_split():
    agent = StubAgent("y" * 5000)
    assert [len(p) for p in logic(agent).handle(42, 100, "q")] == [4096, 904]


def test_commands():
    agent = StubAgent()
    l = logic(agent)
    assert l.handle(42, 100, "/new") == ["Started a new conversation."]
    assert agent.calls == [("reset", "tg:100")]
    assert l.handle(42, 100, "/status") == ["status text"]
    assert "Ask me" in l.handle(42, 100, "/start")[0]
    assert "Ask me" in l.handle(42, 100, "/help@my_music_bot")[0]


def test_unpaired_bot_tells_user_their_id():
    agent = StubAgent()
    reply = logic(agent, allowed=None).handle(12345, 12345, "hello")
    assert "12345" in reply[0] and "telegram_allowed_user_id" in reply[0]
    assert agent.calls == []


def test_scheduler_runs_repeatedly_and_survives_errors():
    calls = []

    def job():
        calls.append(time.monotonic())
        if len(calls) == 1:
            raise RuntimeError("first run fails")

    sched = bot.SnapshotScheduler(0.05, job)
    sched.start()
    time.sleep(0.3)
    sched.stop()
    count = len(calls)
    assert count >= 3
    time.sleep(0.15)
    assert len(calls) == count  # stopped


def test_build_application_registers_handler():
    app = bot.build_application("123456:TEST-TOKEN", logic())
    assert len(app.handlers[0]) == 1


def test_run_bot_requires_token(isolated_home, capsys):
    from music_agent.store import Store

    code = bot.run_bot(Config(timezone="UTC"), Store(isolated_home / "plays.db"))
    assert code == 2
    assert "Telegram is optional" in capsys.readouterr().err
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_bot.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement**

`src/music_agent/bot.py`:

```python
"""Optional Telegram front end, plus the in-process snapshot scheduler used by `run`."""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
from collections.abc import Callable

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from . import music
from .config import Config, get_secret
from .snapshot import run_snapshot
from .store import Store

log = logging.getLogger(__name__)

TELEGRAM_LIMIT = 4096
HELP_TEXT = (
    "Ask me about your Apple Music listening, e.g. \"how many songs did I play today?\" or "
    "\"make a playlist of the rock I played this week\".\n"
    "/new starts a fresh conversation, /status shows recording status and spend."
)
NO_TELEGRAM_MSG = (
    "Telegram is optional and not set up. To use it: create a bot with @BotFather, then run\n"
    "  uv run keyring set music-agent telegram_bot_token\n"
    "and start `music-agent run` again. You can always use `music-agent chat` instead."
)


def split_message(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    parts: list[str] = []
    remaining = text
    while len(remaining) > limit:
        cut = remaining.rfind("\n", 0, limit + 1)
        if cut <= 0:
            parts.append(remaining[:limit])
            remaining = remaining[limit:]
        else:
            parts.append(remaining[:cut])
            remaining = remaining[cut + 1 :]
    parts.append(remaining)
    return [p for p in parts if p]


class BotLogic:
    def __init__(self, agent, allowed_user_id: int | None, status_text: Callable[[], str]):
        self.agent = agent
        self.allowed_user_id = allowed_user_id
        self.status_text = status_text

    def is_allowed(self, user_id: int) -> bool:
        return self.allowed_user_id is not None and user_id == self.allowed_user_id

    def handle(self, user_id: int, chat_id: int, text: str) -> list[str] | None:
        if self.allowed_user_id is None:
            return [
                f"Your Telegram user ID is {user_id}. To let this account use the bot, add\n"
                f"telegram_allowed_user_id = {user_id}\n"
                "to config.toml and restart `music-agent run`."
            ]
        if not self.is_allowed(user_id):
            log.warning("ignored Telegram message from user %s", user_id)
            return None
        conversation = f"tg:{chat_id}"
        stripped = text.strip()
        command = stripped.split()[0].split("@")[0].lower() if stripped.startswith("/") else None
        if command in ("/start", "/help"):
            return [HELP_TEXT]
        if command == "/new":
            self.agent.reset(conversation)
            return ["Started a new conversation."]
        if command == "/status":
            return split_message(self.status_text())
        return split_message(self.agent.respond(conversation, text))


def build_application(token: str, logic: BotLogic) -> Application:
    app = Application.builder().token(token).build()

    async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message is None or update.effective_user is None or update.effective_chat is None:
            return
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        if logic.is_allowed(user_id):
            await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
        replies = await asyncio.to_thread(logic.handle, user_id, chat_id, update.message.text or "")
        for reply in replies or []:
            await update.message.reply_text(reply)

    app.add_handler(MessageHandler(filters.TEXT, on_message))
    return app


class SnapshotScheduler:
    """Runs `job` now and then every `interval_seconds` on a daemon thread until stop()."""

    def __init__(self, interval_seconds: float, job: Callable[[], object]):
        self.interval = interval_seconds
        self.job = job
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="snapshot-scheduler", daemon=True)

    def _loop(self) -> None:
        while True:
            try:
                self.job()
            except Exception:  # noqa: BLE001 - keep scheduling after a failed run
                log.exception("scheduled snapshot failed")
            if self._stop.wait(self.interval):
                return

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)


def run_bot(config: Config, store: Store, no_scheduler: bool = False) -> int:
    from .cli import _require_key, _status_text, build_agent

    token = get_secret("telegram_bot_token")
    if not token:
        print(NO_TELEGRAM_MSG, file=sys.stderr)
        return 2
    key = _require_key()
    if not key:
        return 2
    logic = BotLogic(build_agent(config, store, key), config.telegram_allowed_user_id,
                     lambda: _status_text(config, store))
    scheduler = None
    if not no_scheduler:
        scheduler = SnapshotScheduler(
            config.snapshot_interval_minutes * 60, lambda: run_snapshot(store, music.read_library)
        )
        scheduler.start()
    if config.telegram_allowed_user_id is None:
        print("No telegram_allowed_user_id set: the bot will only reply with each sender's user ID.")
    print("music-agent bot running. Press Ctrl-C to stop.")
    try:
        build_application(token, logic).run_polling()
    finally:
        if scheduler:
            scheduler.stop()
    return 0
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_bot.py tests/test_cli.py -v`
Expected: all pass. If `app.handlers[0]` isn't a list in this python-telegram-bot version, assert on `app.handlers` containing group `0` instead.

- [ ] **Step 5: Full suite and lint**

Run: `uv run pytest && uv run ruff check`
Expected: every non-mac test passes; ruff reports nothing, or only issues you then fix.

- [ ] **Step 6: Commit**

```bash
git add src/music_agent/bot.py tests/test_bot.py
git commit -m "feat: add optional telegram bot and run command"
```

---

### Task 14: README, Mac integration run and live acceptance

**Files:**
- Modify: `README.md`, `docs/superpowers/specs/2026-09-27-apple-music-agent-design.md` (the "Agent harness" row)

**Interfaces:**
- Consumes: the whole Phase 1 CLI.

- [ ] **Step 1: Write the README**

Replace `README.md` with:

````markdown
# music-agent

Ask questions about your Apple Music listening and build playlists, from your terminal or (optionally) Telegram. It runs on your Mac and reads your library from Music.app. **macOS only.**

> Status: Phase 1 (developer preview). A setup wizard, background service and PyPI release are coming in Phase 2.

## What it can do

- "How many songs have I listened to today?" / "…this year?"
- "Make a playlist of what I listened to in the last 24 hours."
- "Make a playlist of the rock I played this past week."
- "Make a rainy-day playlist from what I listened to this week."
- "Who are my most played artists of all time?"

Playlists are created in a **Music Agent** folder in your library. The agent never edits or deletes existing playlists.

## Requirements

- macOS with the Music app and your library on this Mac (turn on **Sync Library** to include plays from your iPhone and other devices)
- [uv](https://docs.astral.sh/uv/)
- An [Anthropic API key](https://console.anthropic.com/). The default model (Claude Haiku 4.5) costs about $0.02 per question; the monthly budget defaults to $5.

## Quickstart (from a clone)

```bash
uv sync
uv run keyring set music-agent anthropic_api_key   # paste your key; stored in the macOS Keychain
uv run music-agent snapshot                         # records a baseline; approve the Music permission prompt
uv run music-agent chat
```

Other commands: `ask "<question>"`, `snapshot`, `status`, `run` (Telegram).

## Why snapshots?

Music.app keeps no listening history, only each song's total play count and last-played date. music-agent takes a snapshot every 10 minutes and compares counts, which builds the history Apple doesn't keep. Counting distinct songs works from day one. **Repeat-play counts and past windows ("last Tuesday") only count from the first snapshot.** Keep `music-agent run` going, or run `music-agent snapshot` regularly (Phase 2 adds a background service).

## Telegram (optional)

1. In Telegram, message **@BotFather**, send `/newbot`, and copy the token.
2. `uv run keyring set music-agent telegram_bot_token`
3. `uv run music-agent run`, then message your bot. It replies with your user ID.
4. Add `telegram_allowed_user_id = <your id>` to `~/Library/Application Support/music-agent/config.toml` and restart `run`.

## Configuration

`~/Library/Application Support/music-agent/config.toml`:

```toml
timezone = "America/Los_Angeles"   # detected automatically
model = "claude-haiku-4-5"         # e.g. "claude-sonnet-5" for better themed playlists
monthly_budget_usd = 5.0
telegram_allowed_user_id = 123456789
snapshot_interval_minutes = 10
playlist_folder = "Music Agent"
```

## Limitations

- Songs you stream without adding them to your library aren't visible in Music.app, so they can't be counted.
- Plays from other devices arrive through iCloud Sync Library (about a minute in testing, but Apple doesn't guarantee it). `music-agent status` shows the measured lag.
- The Mac must be on and awake for Telegram answers.
- Themed playlists use Claude's knowledge of the songs; there's no audio analysis.

## Privacy

Song titles, artists, albums and genres in tool results are sent to Anthropic's API to answer your questions. If Telegram is enabled, messages pass through Telegram's servers. Nothing else leaves your Mac.

## Development

```bash
uv run pytest            # unit tests (never touch your library)
uv run pytest -m mac     # integration tests against your real Music library (creates and deletes a test playlist)
uv run ruff check
```
````

- [ ] **Step 2: Update the spec's harness decision**

In `docs/superpowers/specs/2026-09-27-apple-music-agent-design.md`, replace the "Agent harness" table row with:

```markdown
| Agent harness | Own Python bot + a hand-written tool-use loop over the Claude Messages API | Testable end to end, runs as a plain launchd process, the model can only call the tools we pass, no research-preview dependency, no MCP needed. The loop is hand-written rather than the SDK's beta tool runner so history and per-call usage can be persisted and the client faked in tests. |
```

- [ ] **Step 3: Run the Mac integration tests**

Run: `uv run pytest -m mac -v`
Expected: all pass, and no "zz music-agent test" items remain in Music.app.

- [ ] **Step 4: Live acceptance (needs your API key; costs about $0.10)**

Run each command and check the reply against the expectation:

| Command | Expected |
|---|---|
| `uv run music-agent ask "How many songs have I listened to today?"` | A distinct-song count consistent with `played_tracks` for today, not an invented number |
| `uv run music-agent ask "How many songs have I listened to this year?"` | Distinct songs this year (2,542 or more as of Sep 27); mentions that repeat plays are only counted since install |
| `uv run music-agent ask "Create a playlist of the songs I listened to in the last 24 hours"` | A new playlist in the **Music Agent** folder with the reported track count |
| `uv run music-agent ask "Create a playlist of the rock songs I listened to in the past week"` | A playlist of rock-family tracks; the reply lists which genres counted |
| `uv run music-agent ask "Create a late-night driving playlist from what I listened to this week"` | A themed playlist, or an honest "only N fit" with an offer to widen |

Then run `uv run music-agent status` and confirm the spend line shows the cost of the questions above.

- [ ] **Step 5: Commit**

```bash
git add README.md docs/superpowers/specs/2026-09-27-apple-music-agent-design.md
git commit -m "docs: add phase 1 readme and record harness decision"
```

- [ ] **Step 6: Verify against the spec**

Invoke the `verify` skill to test the finished branch in an isolated worktree against the spec's Phase 1 scope.
