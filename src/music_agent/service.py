"""launchd jobs that keep music-agent recording (and the optional bot running) in the background."""

from __future__ import annotations

import os
import plistlib
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .config import Config, log_dir

SNAPSHOT_LABEL = "io.music-agent.snapshot"
BOT_LABEL = "io.music-agent.bot"
LABELS = (SNAPSHOT_LABEL, BOT_LABEL)
Runner = Callable[[list[str]], subprocess.CompletedProcess]


class ServiceError(RuntimeError):
    """A launchd job couldn't be installed or found."""


def _run_command(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, encoding="utf-8", timeout=30, check=False)


_default_runner = _run_command  # tests replace this with a refusal (see conftest)


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
    args = [str(exe), "run", "--no-scheduler", "--service"]
    if keep_awake:
        args = ["/usr/bin/caffeinate", "-s", *args]  # stay awake while on AC power
    return {
        "Label": BOT_LABEL,
        "ProgramArguments": args,
        "RunAtLoad": True,
        # Restart after crashes, but not after a clean exit: `run --service` exits 0 when
        # Telegram isn't configured, so a removed token can't cause an endless restart loop.
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 30,
        "StandardOutPath": str(logs / "bot.out.log"),
        "StandardErrorPath": str(logs / "bot.err.log"),
        **_environment(),
    }


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _bootout(label: str, run: Runner) -> None:
    """Unload a job. "Not loaded" is fine; any other failure means it may still be running."""
    proc = run(["/bin/launchctl", "bootout", f"{_domain()}/{label}"])
    detail = (proc.stderr or proc.stdout or "").strip()
    not_loaded = proc.returncode in (3, 113) or "No such process" in detail or (
        "Could not find service" in detail
    )
    if proc.returncode != 0 and not not_loaded:
        raise ServiceError(f"launchctl couldn't unload {label}: {detail}")


def _load(job: dict, directory: Path, run: Runner) -> None:
    label = job["Label"]
    directory.mkdir(parents=True, exist_ok=True)  # missing on a Mac that never had agents
    _bootout(label, run)  # raises before touching the old job file if it can't be unloaded
    path = plist_path(label, directory)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(plistlib.dumps(job))
    os.replace(tmp, path)
    proc = run(["/bin/launchctl", "bootstrap", _domain(), str(path)])
    if proc.returncode != 0:
        path.unlink(missing_ok=True)  # an unloadable job must not reappear at next login
        detail = (proc.stderr or proc.stdout or "").strip()
        raise ServiceError(f"launchctl couldn't load {label}: {detail}")


def _unload(label: str, directory: Path, run: Runner) -> bool:
    _bootout(label, run)  # keeps the job file if the job couldn't really be stopped
    path = plist_path(label, directory)
    if path.exists():
        path.unlink()
        return True
    return False


@dataclass(frozen=True)
class InstallResult:
    installed: list[str]
    removed: list[str]
    keep_awake: bool
    logs: Path


def install(
    config: Config,
    telegram_configured: bool,
    runner: Runner | None = None,
    directory: Path | None = None,
    exe: Path | None = None,
    battery: bool | None = None,
) -> InstallResult:
    run = runner or _default_runner
    directory = directory or agents_dir()
    exe = exe or executable_path()
    logs = log_dir()
    logs.mkdir(parents=True, exist_ok=True)
    keep_awake = has_battery(run) if battery is None else battery
    _load(snapshot_job(exe, config, logs), directory, run)
    installed, removed = [SNAPSHOT_LABEL], []
    if telegram_configured:
        try:
            _load(bot_job(exe, logs, keep_awake), directory, run)
        except ServiceError as exc:
            raise ServiceError(
                f"Background snapshots were installed, but the Telegram bot job wasn't: {exc}"
            ) from exc
        installed.append(BOT_LABEL)
    elif _unload(BOT_LABEL, directory, run):
        removed.append(BOT_LABEL)
    return InstallResult(installed, removed, keep_awake and telegram_configured, logs)


def uninstall(runner: Runner | None = None, directory: Path | None = None) -> list[str]:
    run = runner or _default_runner
    directory = directory or agents_dir()
    return [label for label in LABELS if _unload(label, directory, run)]


@dataclass(frozen=True)
class JobStatus:
    label: str
    installed: bool
    loaded: bool
    running: bool
    last_exit: int | None  # negative = killed by that signal
    program: str | None = None  # the music-agent the job runs
    program_exists: bool = True
    interval_s: int | None = None  # what launchd actually uses (snapshot job)
    error: str | None = None  # launchctl itself failed


_EXIT_RE = re.compile(r"last exit code = (-?\d+)")
_SIGNAL_RE = re.compile(r"last terminating signal = .*?(\d+)\s*$")
_INTERVAL_RE = re.compile(r"run interval = (\d+) seconds")


def _parse_print(text: str) -> tuple[bool, int | None]:
    """launchd prints `last exit code = 0`, `= 78: EX_CONFIG`, `= (never exited)`, or
    `last terminating signal = Killed: 9` for a job killed by a signal."""
    running: bool | None = None
    last_exit: int | None = None
    for line in text.splitlines():
        item = line.strip()
        if running is None and item.startswith("state = "):
            running = item == "state = running"  # first state line is the job's own
        if last_exit is None:
            if match := _EXIT_RE.match(item):
                last_exit = int(match.group(1))
            elif match := _SIGNAL_RE.match(item):
                last_exit = -int(match.group(1))
    return bool(running), last_exit


def _job_program(path: Path) -> str | None:
    try:
        args = plistlib.loads(path.read_bytes())["ProgramArguments"]
    except (OSError, KeyError, plistlib.InvalidFileException):
        return None
    if args and args[0] == "/usr/bin/caffeinate":
        args = args[2:]
    return args[0] if args else None


def status(runner: Runner | None = None, directory: Path | None = None) -> list[JobStatus]:
    run = runner or _default_runner
    out = []
    for label in LABELS:
        path = plist_path(label, directory)
        installed = path.exists()
        program = _job_program(path) if installed else None
        exists = program is None or Path(program).exists()
        try:
            proc = run(["/bin/launchctl", "print", f"{_domain()}/{label}"])
        except (OSError, subprocess.TimeoutExpired) as exc:
            out.append(JobStatus(label, installed, False, False, None, program, exists, None,
                                 str(exc)))
            continue
        loaded = proc.returncode == 0
        text = proc.stdout or ""
        running, last_exit = _parse_print(text) if loaded else (False, None)
        interval = _INTERVAL_RE.search(text) if loaded else None
        out.append(
            JobStatus(label, installed, loaded, running, last_exit, program, exists,
                      int(interval.group(1)) if interval else None)
        )
    return out
