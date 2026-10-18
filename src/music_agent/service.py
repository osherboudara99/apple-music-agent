"""launchd jobs that keep music-agent recording (and the optional bot running) in the background."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from .config import Config

SNAPSHOT_LABEL = "io.music-agent.snapshot"
BOT_LABEL = "io.music-agent.bot"
LABELS = (SNAPSHOT_LABEL, BOT_LABEL)
Runner = Callable[[list[str]], subprocess.CompletedProcess]


class ServiceError(RuntimeError):
    """A launchd job couldn't be installed or found."""


def _default_runner(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, encoding="utf-8", check=False)


def agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def plist_path(label: str, directory: Path | None = None) -> Path:
    return (directory or agents_dir()) / f"{label}.plist"


def executable_path() -> Path:
    """The music-agent command the jobs should run: the one installed with this Python."""
    candidate = Path(sys.executable).parent / "music-agent"
    if candidate.exists():
        return candidate
    found = shutil.which("music-agent")
    if found:
        return Path(found)
    raise ServiceError(
        "Couldn't find the music-agent executable. Install it (uv tool install music-agent) "
        "or run this from the project with `uv run music-agent service install`."
    )


def has_battery(runner: Runner | None = None) -> bool:
    out = (runner or _default_runner)(["/usr/bin/pmset", "-g", "batt"]).stdout or ""
    return "InternalBattery" in out


def _environment() -> dict:
    # Only the data-folder override is passed through. Secrets stay in the Keychain and are
    # never written into a job file on disk.
    home = os.environ.get("MUSIC_AGENT_HOME")
    return {"EnvironmentVariables": {"MUSIC_AGENT_HOME": home}} if home else {}


def snapshot_job(exe: Path, config: Config, logs: Path) -> dict:
    return {
        "Label": SNAPSHOT_LABEL,
        "ProgramArguments": [str(exe), "snapshot"],
        "StartInterval": config.snapshot_interval_minutes * 60,
        "RunAtLoad": True,
        "ProcessType": "Background",
        "StandardOutPath": str(logs / "snapshot.out.log"),
        "StandardErrorPath": str(logs / "snapshot.err.log"),
        **_environment(),
    }


def bot_job(exe: Path, logs: Path, keep_awake: bool) -> dict:
    args = [str(exe), "run", "--no-scheduler"]
    if keep_awake:
        args = ["/usr/bin/caffeinate", "-s", *args]  # stay awake while on AC power
    return {
        "Label": BOT_LABEL,
        "ProgramArguments": args,
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 30,
        "StandardOutPath": str(logs / "bot.out.log"),
        "StandardErrorPath": str(logs / "bot.err.log"),
        **_environment(),
    }
