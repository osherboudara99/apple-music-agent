# music-agent Phase 2a (Background Service) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `music-agent service install|uninstall|status` so plays are recorded every few minutes by launchd (the macOS service manager) without any command running, and so the optional Telegram bot starts at login and restarts if it crashes.

**Architecture:**
- A new `service.py` builds two launchd job definitions as plain dicts (pure functions, compared against expected dicts in tests). It writes them to `~/Library/LaunchAgents/` and loads or unloads them with `launchctl bootstrap` / `bootout`.
- Every `launchctl` and `pmset` call goes through an injectable runner, so unit tests never touch the real launchd.
- `cli.py` gains the `service` command, and `status` reports whether the jobs are installed and how their last run went.

**Tech Stack:** Python ≥ 3.11 standard library (`plistlib`, `subprocess`, `os`), launchd via `/bin/launchctl`, `/usr/bin/pmset`, `/usr/bin/caffeinate`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-27-apple-music-agent-design.md`, sections "launchd service", "CLI" and "Open items" (item 2). This plan covers only the service part of Phase 2. The wizard, `doctor`, docs, CI and release get their own plans.

## Global Constraints

- Job labels: `io.music-agent.snapshot` and `io.music-agent.bot`. Job files go in `~/Library/LaunchAgents/<label>.plist`. Create that folder if it's missing: it didn't exist on the author's Mac (verified 2026-10-18).
- Snapshot job: `ProgramArguments = [<exe>, "snapshot"]`, `StartInterval = snapshot_interval_minutes × 60`, `RunAtLoad = true`, `ProcessType = Background`.
- Bot job: installed only when a Telegram bot token is configured. `ProgramArguments = [<exe>, "run", "--no-scheduler"]`, prefixed with `/usr/bin/caffeinate -s` when the Mac has a battery. Also `RunAtLoad = true`, `KeepAlive = true`, `ThrottleInterval = 30`.
- `<exe>` is the `music-agent` executable next to the running Python (`Path(sys.executable).parent / "music-agent"`, e.g. the repo's `.venv/bin/music-agent` or the uv-tool install). The fallback is `shutil.which`. Install fails with a clear error if neither exists.
- Job logs go to `log_dir()` (`~/Library/Application Support/music-agent/logs/`), as `snapshot.out.log`, `snapshot.err.log`, `bot.out.log` and `bot.err.log`.
- **Secrets are never written into a job file.** The only environment variable passed through is `MUSIC_AGENT_HOME`, and only when it is set at install time.
- Verified 2026-10-18: a launchd-started `music-agent snapshot` read Music.app with exit code 0 and no prompt (open item 2). Other machines may get a one-time Automation prompt; `doctor` (a later plan) will explain how to grant it.
- Same conventions as Phase 1: TDD, `uv run pytest` / `uv run ruff check`, conventional commits with **no AI attribution lines**, and unit tests never touch real launchd or the real Music library.

## Review Focus

1. **`~/Library/LaunchAgents` doesn't exist** (a fresh Mac): install creates it. Test in Task 2.
2. **Running `service install` a second time** (for example after changing the interval): each job is unloaded before it's loaded, so launchd never reports "already loaded", and the new interval takes effect. Test in Task 2.
3. **Secrets set as environment variables at install time** (`MUSIC_AGENT_ANTHROPIC_API_KEY`) must not end up in the job files on disk. Test in Task 1.
4. **The executable can't be found** (for example the clone was moved): install fails with a message saying how to fix it, and writes no job pointing nowhere. Test in Task 1.
5. **Uninstalling when nothing is installed**, or disabling Telegram after the bot job was installed: no error, and a stale bot job is removed on the next install. Test in Task 2.

---

## File Structure

```
src/music_agent/service.py      # job definitions, install/uninstall/status via launchctl
src/music_agent/cli.py          # + `service` command; `status` shows the jobs
tests/test_service.py           # job dicts, install/uninstall/status with a fake runner
tests/test_cli.py               # + service command tests
README.md                       # + "Run it in the background" section
docs/superpowers/specs/2026-09-27-apple-music-agent-design.md   # open item 2 resolved
```

---

### Task 1: Job definitions and environment detection

**Files:**
- Create: `src/music_agent/service.py`
- Test: `tests/test_service.py`

**Interfaces:**
- Consumes: `Config` and `log_dir()` from `config.py` (Phase 1).
- Produces:
  - `SNAPSHOT_LABEL`, `BOT_LABEL`, `LABELS`
  - `class ServiceError(RuntimeError)`
  - `Runner = Callable[[list[str]], subprocess.CompletedProcess]`
  - `agents_dir() -> Path`, `plist_path(label: str, directory: Path | None = None) -> Path`
  - `executable_path() -> Path`, which raises `ServiceError`
  - `has_battery(runner: Runner | None = None) -> bool`
  - `snapshot_job(exe: Path, config: Config, logs: Path) -> dict`
  - `bot_job(exe: Path, logs: Path, keep_awake: bool) -> dict`

- [ ] **Step 1: Write the failing tests**

`tests/test_service.py`:

```python
import subprocess
from pathlib import Path

import pytest

from music_agent import service
from music_agent.config import Config

EXE = Path("/Users/me/.local/bin/music-agent")
LOGS = Path("/Users/me/Library/Application Support/music-agent/logs")


def completed(stdout="", returncode=0, stderr=""):
    return lambda args: subprocess.CompletedProcess(args, returncode, stdout, stderr)


def test_snapshot_job():
    job = service.snapshot_job(EXE, Config(timezone="UTC", snapshot_interval_minutes=15), LOGS)
    assert job == {
        "Label": "io.music-agent.snapshot",
        "ProgramArguments": [str(EXE), "snapshot"],
        "StartInterval": 900,
        "RunAtLoad": True,
        "ProcessType": "Background",
        "StandardOutPath": str(LOGS / "snapshot.out.log"),
        "StandardErrorPath": str(LOGS / "snapshot.err.log"),
    }


def test_bot_job_keeps_laptops_awake():
    job = service.bot_job(EXE, LOGS, keep_awake=True)
    assert job["ProgramArguments"] == ["/usr/bin/caffeinate", "-s", str(EXE), "run", "--no-scheduler"]
    assert job["KeepAlive"] is True and job["RunAtLoad"] is True and job["ThrottleInterval"] == 30
    assert job["Label"] == "io.music-agent.bot"
    assert job["StandardErrorPath"] == str(LOGS / "bot.err.log")


def test_bot_job_on_desktop_runs_directly():
    assert service.bot_job(EXE, LOGS, keep_awake=False)["ProgramArguments"] == [
        str(EXE), "run", "--no-scheduler",
    ]


def test_only_music_agent_home_is_passed_to_jobs(monkeypatch):
    monkeypatch.setenv("MUSIC_AGENT_HOME", "/tmp/ma-home")
    monkeypatch.setenv("MUSIC_AGENT_ANTHROPIC_API_KEY", "sk-secret")
    monkeypatch.setenv("MUSIC_AGENT_TELEGRAM_BOT_TOKEN", "123:secret")
    for job in (
        service.snapshot_job(EXE, Config(timezone="UTC"), LOGS),
        service.bot_job(EXE, LOGS, keep_awake=False),
    ):
        assert job["EnvironmentVariables"] == {"MUSIC_AGENT_HOME": "/tmp/ma-home"}
        assert "secret" not in repr(job)


def test_no_environment_block_without_music_agent_home(monkeypatch):
    monkeypatch.delenv("MUSIC_AGENT_HOME", raising=False)
    assert "EnvironmentVariables" not in service.snapshot_job(EXE, Config(timezone="UTC"), LOGS)


def test_has_battery():
    laptop = "Now drawing from 'AC Power'\n -InternalBattery-0 (id=1)\t100%; charged;"
    assert service.has_battery(completed(laptop)) is True
    assert service.has_battery(completed("Now drawing from 'AC Power'\n")) is False


def test_executable_next_to_python(monkeypatch, tmp_path):
    exe = tmp_path / "bin" / "music-agent"
    exe.parent.mkdir()
    exe.write_text("#!/bin/sh\n")
    monkeypatch.setattr(service.sys, "executable", str(tmp_path / "bin" / "python3"))
    assert service.executable_path() == exe


def test_executable_falls_back_to_path(monkeypatch, tmp_path):
    monkeypatch.setattr(service.sys, "executable", str(tmp_path / "python3"))
    monkeypatch.setattr(service.shutil, "which", lambda name: "/opt/bin/music-agent")
    assert service.executable_path() == Path("/opt/bin/music-agent")


def test_missing_executable_is_a_clear_error(monkeypatch, tmp_path):
    monkeypatch.setattr(service.sys, "executable", str(tmp_path / "python3"))
    monkeypatch.setattr(service.shutil, "which", lambda name: None)
    with pytest.raises(service.ServiceError, match="music-agent executable"):
        service.executable_path()


def test_plist_path(tmp_path):
    assert service.plist_path("io.music-agent.bot", tmp_path) == tmp_path / "io.music-agent.bot.plist"
    assert service.agents_dir() == Path.home() / "Library" / "LaunchAgents"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_service.py -q`
Expected: collection error, `ImportError: cannot import name 'service'`.

- [ ] **Step 3: Implement**

`src/music_agent/service.py`:

```python
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
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_service.py -q && uv run ruff check`
Expected: all pass; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/music_agent/service.py tests/test_service.py
git commit -m "feat: add launchd job definitions for the background service"
```

---

### Task 2: Install, uninstall and status via launchctl

**Files:**
- Modify: `src/music_agent/service.py` (append)
- Test: `tests/test_service.py` (append)

**Interfaces:**
- Consumes: everything from Task 1; `log_dir()` from `config.py`.
- Produces:
  - `@dataclass(frozen=True) InstallResult(installed: list[str], removed: list[str], keep_awake: bool, logs: Path)`
  - `install(config: Config, telegram_configured: bool, runner: Runner | None = None, directory: Path | None = None, exe: Path | None = None, battery: bool | None = None) -> InstallResult`
  - `uninstall(runner: Runner | None = None, directory: Path | None = None) -> list[str]`, returning the labels whose job files were removed
  - `@dataclass(frozen=True) JobStatus(label: str, installed: bool, loaded: bool, running: bool, last_exit: int | None)`
  - `status(runner: Runner | None = None, directory: Path | None = None) -> list[JobStatus]`

`launchctl` behavior relied on:
- `bootstrap gui/<uid> <plist>` loads a job and fails if it's already loaded.
- `bootout gui/<uid>/<label>` unloads one, and returns non-zero if it isn't loaded; that's ignored.
- `print gui/<uid>/<label>` exits 0 only when the job is loaded. The first `state = …` line is the job's own state; nested blocks repeat `state = active`. `last exit code = N` may read `(never exited)`.

- [ ] **Step 1: Write the failing tests (append to `tests/test_service.py`)**

```python
import os
import plistlib

from music_agent.service import BOT_LABEL, SNAPSHOT_LABEL

DOMAIN = f"gui/{os.getuid()}"


class FakeLaunchctl:
    def __init__(self, loaded=(), bootstrap_rc=0):
        self.loaded = set(loaded)
        self.calls = []
        self.bootstrap_rc = bootstrap_rc

    def __call__(self, args):
        self.calls.append(args)
        if args[0] == "/usr/bin/pmset":
            return subprocess.CompletedProcess(args, 0, "", "")
        verb = args[1]
        if verb == "bootout":
            label = args[2].split("/")[-1]
            was = label in self.loaded
            self.loaded.discard(label)
            return subprocess.CompletedProcess(args, 0 if was else 3, "", "No such process")
        if verb == "bootstrap":
            label = Path(args[3]).stem
            if label in self.loaded:
                return subprocess.CompletedProcess(args, 5, "", "Input/output error")
            if self.bootstrap_rc:
                return subprocess.CompletedProcess(args, self.bootstrap_rc, "", "Bootstrap failed")
            self.loaded.add(label)
            return subprocess.CompletedProcess(args, 0, "", "")
        if verb == "print":
            label = args[2].split("/")[-1]
            if label not in self.loaded:
                return subprocess.CompletedProcess(args, 113, "", "Could not find service")
            text = (
                f"{DOMAIN}/{label} = {{\n\tstate = not running\n\tlast exit code = 0\n"
                "\tendpoints = {\n\t\tstate = active\n\t}\n}\n"
            )
            return subprocess.CompletedProcess(args, 0, text, "")
        raise AssertionError(args)


def test_install_creates_agents_folder_and_loads_snapshot_job(tmp_path):
    agents = tmp_path / "LaunchAgents"  # doesn't exist yet
    fake = FakeLaunchctl()
    result = service.install(Config(timezone="UTC"), False, fake, agents, EXE, battery=False)
    path = agents / f"{SNAPSHOT_LABEL}.plist"
    assert plistlib.loads(path.read_bytes())["ProgramArguments"] == [str(EXE), "snapshot"]
    assert ["/bin/launchctl", "bootstrap", DOMAIN, str(path)] in fake.calls
    assert result.installed == [SNAPSHOT_LABEL] and result.removed == []
    assert not (agents / f"{BOT_LABEL}.plist").exists()


def test_reinstall_reloads_with_new_settings(tmp_path):
    fake = FakeLaunchctl()
    service.install(Config(timezone="UTC"), False, fake, tmp_path, EXE, battery=False)
    service.install(
        Config(timezone="UTC", snapshot_interval_minutes=30), False, fake, tmp_path, EXE, battery=False
    )
    job = plistlib.loads((tmp_path / f"{SNAPSHOT_LABEL}.plist").read_bytes())
    assert job["StartInterval"] == 1800
    assert SNAPSHOT_LABEL in fake.loaded  # bootout happened before the second bootstrap


def test_install_with_telegram_adds_bot_job(tmp_path):
    fake = FakeLaunchctl()
    result = service.install(Config(timezone="UTC"), True, fake, tmp_path, EXE, battery=True)
    assert result.installed == [SNAPSHOT_LABEL, BOT_LABEL] and result.keep_awake is True
    bot = plistlib.loads((tmp_path / f"{BOT_LABEL}.plist").read_bytes())
    assert bot["ProgramArguments"][:2] == ["/usr/bin/caffeinate", "-s"]


def test_install_without_telegram_removes_a_stale_bot_job(tmp_path):
    fake = FakeLaunchctl()
    service.install(Config(timezone="UTC"), True, fake, tmp_path, EXE, battery=False)
    result = service.install(Config(timezone="UTC"), False, fake, tmp_path, EXE, battery=False)
    assert result.removed == [BOT_LABEL]
    assert BOT_LABEL not in fake.loaded and not (tmp_path / f"{BOT_LABEL}.plist").exists()


def test_install_reports_launchctl_failure(tmp_path):
    with pytest.raises(service.ServiceError, match="Bootstrap failed"):
        service.install(
            Config(timezone="UTC"), False, FakeLaunchctl(bootstrap_rc=5), tmp_path, EXE, battery=False
        )


def test_install_leaves_no_temp_files(tmp_path):
    service.install(Config(timezone="UTC"), False, FakeLaunchctl(), tmp_path, EXE, battery=False)
    assert sorted(p.name for p in tmp_path.iterdir()) == [f"{SNAPSHOT_LABEL}.plist"]


def test_uninstall(tmp_path):
    fake = FakeLaunchctl()
    service.install(Config(timezone="UTC"), True, fake, tmp_path, EXE, battery=False)
    assert service.uninstall(fake, tmp_path) == [SNAPSHOT_LABEL, BOT_LABEL]
    assert fake.loaded == set() and list(tmp_path.iterdir()) == []


def test_uninstall_when_nothing_is_installed(tmp_path):
    assert service.uninstall(FakeLaunchctl(), tmp_path) == []


def test_status(tmp_path):
    fake = FakeLaunchctl()
    service.install(Config(timezone="UTC"), False, fake, tmp_path, EXE, battery=False)
    snap, bot = service.status(fake, tmp_path)
    assert snap == service.JobStatus(SNAPSHOT_LABEL, True, True, False, 0)
    assert bot == service.JobStatus(BOT_LABEL, False, False, False, None)


def test_status_parses_running_and_never_exited():
    text = "gui/501/x = {\n\tstate = running\n\tpid = 4242\n\tlast exit code = (never exited)\n}\n"
    assert service._parse_print(text) == (True, None)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_service.py -q`
Expected: the new tests fail with `AttributeError: module 'music_agent.service' has no attribute 'install'`.

- [ ] **Step 3: Implement (append to `src/music_agent/service.py`)**

Add `import plistlib`, `from dataclasses import dataclass` and `from .config import Config, log_dir` (replacing the existing `Config` import), then append:

```python
def _domain() -> str:
    return f"gui/{os.getuid()}"


def _load(job: dict, directory: Path, run: Runner) -> None:
    label = job["Label"]
    directory.mkdir(parents=True, exist_ok=True)  # missing on a Mac that never had agents
    run(["/bin/launchctl", "bootout", f"{_domain()}/{label}"])  # not loaded: ignored
    path = plist_path(label, directory)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(plistlib.dumps(job))
    os.replace(tmp, path)
    proc = run(["/bin/launchctl", "bootstrap", _domain(), str(path)])
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise ServiceError(f"launchctl couldn't load {label}: {detail}")


def _unload(label: str, directory: Path, run: Runner) -> bool:
    run(["/bin/launchctl", "bootout", f"{_domain()}/{label}"])
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
        _load(bot_job(exe, logs, keep_awake), directory, run)
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
    last_exit: int | None


def _parse_print(text: str) -> tuple[bool, int | None]:
    running: bool | None = None
    last_exit: int | None = None
    seen_exit = False
    for line in text.splitlines():
        item = line.strip()
        if running is None and item.startswith("state = "):
            running = item == "state = running"  # first state line is the job's own
        if not seen_exit and item.startswith("last exit code = "):
            seen_exit = True
            value = item.removeprefix("last exit code = ").split()[0]
            last_exit = int(value) if value.lstrip("-").isdigit() else None
    return bool(running), last_exit


def status(runner: Runner | None = None, directory: Path | None = None) -> list[JobStatus]:
    run = runner or _default_runner
    out = []
    for label in LABELS:
        installed = plist_path(label, directory).exists()
        proc = run(["/bin/launchctl", "print", f"{_domain()}/{label}"])
        loaded = proc.returncode == 0
        running, last_exit = _parse_print(proc.stdout or "") if loaded else (False, None)
        out.append(JobStatus(label, installed, loaded, running, last_exit))
    return out
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_service.py -q && uv run ruff check`
Expected: all pass; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/music_agent/service.py tests/test_service.py
git commit -m "feat: install, uninstall and inspect launchd jobs"
```

---

### Task 3: `service` command and service lines in `status`

**Files:**
- Modify: `src/music_agent/cli.py`
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- Consumes: `service.install`, `uninstall`, `status`, `JobStatus`, `ServiceError`, `SNAPSHOT_LABEL`, `BOT_LABEL` (Tasks 1–2); `get_secret` (Phase 1).
- Produces:
  - `format_service_status(statuses: list[service.JobStatus], config: Config) -> str`
  - `format_status(summary, spent, config, services=None)`, extended with an optional service list
  - `music-agent service install|uninstall|status`

- [ ] **Step 1: Write the failing tests (append to `tests/test_cli.py`)**

```python
from pathlib import Path

from music_agent import service


def job(label, installed=True, loaded=True, running=False, last_exit=0):
    return service.JobStatus(label, installed, loaded, running, last_exit)


@pytest.fixture(autouse=True)
def no_real_launchd(monkeypatch):
    """CLI tests never query the real launchd: `status` now includes service lines."""
    monkeypatch.setattr(
        service, "status",
        lambda: [job(service.SNAPSHOT_LABEL, False, False, False, None),
                 job(service.BOT_LABEL, False, False, False, None)],
    )


@pytest.fixture
def fake_service(monkeypatch):
    calls = {}

    def install(config, telegram_configured):
        calls["install"] = telegram_configured
        labels = [service.SNAPSHOT_LABEL] + ([service.BOT_LABEL] if telegram_configured else [])
        return service.InstallResult(labels, [], False, Path("/logs"))

    monkeypatch.setattr(service, "install", install)
    monkeypatch.setattr(service, "uninstall", lambda: [service.SNAPSHOT_LABEL])
    monkeypatch.setattr(
        service, "status",
        lambda: [job(service.SNAPSHOT_LABEL), job(service.BOT_LABEL, False, False, False, None)],
    )
    return calls


def test_service_install_without_telegram(fake_service, capsys):
    assert cli.main(["service", "install"]) == 0
    out = capsys.readouterr().out
    assert fake_service["install"] is False
    assert "every 10 minutes" in out and "/logs" in out and "Telegram" in out


def test_service_install_with_telegram(fake_service, capsys):
    set_secret("telegram_bot_token", "123:abc")
    assert cli.main(["service", "install"]) == 0
    assert fake_service["install"] is True
    assert "bot" in capsys.readouterr().out.lower()


def test_service_install_error(monkeypatch, capsys):
    def boom(config, telegram_configured):
        raise service.ServiceError("launchctl couldn't load io.music-agent.snapshot: nope")

    monkeypatch.setattr(service, "install", boom)
    assert cli.main(["service", "install"]) == 1
    assert "nope" in capsys.readouterr().err


def test_service_uninstall(fake_service, capsys):
    assert cli.main(["service", "uninstall"]) == 0
    assert "Removed" in capsys.readouterr().out


def test_service_status(fake_service, capsys):
    assert cli.main(["service", "status"]) == 0
    out = capsys.readouterr().out
    assert "Background snapshots: on" in out and "Telegram bot service: not installed" in out


def test_format_service_status_variants():
    cfg = Config(timezone="UTC")
    off = cli.format_service_status(
        [job(service.SNAPSHOT_LABEL, False, False, False, None),
         job(service.BOT_LABEL, False, False, False, None)], cfg,
    )
    assert "Background snapshots: off" in off and "music-agent service install" in off
    failing = cli.format_service_status(
        [job(service.SNAPSHOT_LABEL, last_exit=1), job(service.BOT_LABEL, running=True)], cfg
    )
    assert "last run failed (exit 1)" in failing and "Telegram bot service: running" in failing


def test_status_includes_service_lines(fake_service, monkeypatch, capsys):
    monkeypatch.setattr(music, "read_library", lambda: [make_track("A", played_count=1)])
    cli.main(["snapshot"])
    capsys.readouterr()
    assert cli.main(["status"]) == 0
    assert "Background snapshots: on" in capsys.readouterr().out
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_cli.py -q`
Expected: the new tests fail. argparse rejects `service` (`SystemExit: 2`), and `format_service_status` doesn't exist.

- [ ] **Step 3: Implement in `src/music_agent/cli.py`**

1. Add `service` to the import line: `from . import music, queries, service`.

2. Add these helpers after `format_status`:

```python
def format_service_status(statuses: list[service.JobStatus], config: Config) -> str:
    by_label = {s.label: s for s in statuses}
    snap = by_label[service.SNAPSHOT_LABEL]
    if not (snap.installed and snap.loaded):
        snap_line = (
            "Background snapshots: off (plays are only recorded while a command runs). "
            "Turn on with `music-agent service install`."
        )
    elif snap.last_exit not in (None, 0):
        snap_line = (
            f"Background snapshots: on, but the last run failed (exit {snap.last_exit}); "
            "see logs/snapshot.err.log"
        )
    else:
        snap_line = f"Background snapshots: on (every {config.snapshot_interval_minutes} minutes)"
    bot = by_label[service.BOT_LABEL]
    if not bot.installed:
        bot_line = "Telegram bot service: not installed"
    elif bot.running:
        bot_line = "Telegram bot service: running"
    else:
        bot_line = "Telegram bot service: installed but not running; see logs/bot.err.log"
    return f"{snap_line}\n{bot_line}"


def _cmd_service(action: str, config: Config) -> int:
    try:
        if action == "install":
            telegram = bool(get_secret("telegram_bot_token"))
            result = service.install(config, telegram)
            print(
                f"Background snapshots installed: every {config.snapshot_interval_minutes} "
                "minutes, and at login."
            )
            if service.BOT_LABEL in result.installed:
                awake = " (keeps the Mac awake while plugged in)" if result.keep_awake else ""
                print(f"Telegram bot installed: starts at login and restarts if it stops{awake}.")
            else:
                print("Telegram bot not installed (no bot token). Re-run after setting one up.")
            if service.BOT_LABEL in result.removed:
                print("Removed the old Telegram bot job.")
            print(f"Logs: {result.logs}")
            return 0
        if action == "uninstall":
            removed = service.uninstall()
            print("Removed: " + ", ".join(removed) if removed else "Nothing was installed.")
            return 0
        print(format_service_status(service.status(), config))
        return 0
    except service.ServiceError as exc:
        print(f"Service error: {exc}", file=sys.stderr)
        return 1
```

3. Make `format_status` accept the service list, and append it at the end:

```python
def format_status(
    summary: dict, spent: float, config: Config, services: list | None = None
) -> str:
```

At the end of `format_status`, before `return`, add:

```python
    if services is not None:
        lines.append(format_service_status(services, config))
```

In `_status_text`, pass `services=service.status()`:

```python
    return format_status(queries.status_summary(store, now), spent, config, service.status())
```

The early return for "No snapshots yet" stays as it is.

4. In `_parser()`, add the subcommand:

```python
    svc = sub.add_parser("service", help="record plays in the background (and run the bot)")
    svc.add_argument("action", choices=["install", "uninstall", "status"])
```

5. In `main()`, before the `chat`/`ask` branch:

```python
    if args.command == "service":
        return _cmd_service(args.action, config)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q && uv run ruff check`
Expected: the whole suite passes; ruff clean. The autouse `no_real_launchd` fixture keeps every CLI test, including the existing `status` tests, away from the real launchd.

- [ ] **Step 5: Commit**

```bash
git add src/music_agent/cli.py tests/test_cli.py
git commit -m "feat: add service command and show background jobs in status"
```

---

### Task 4: Docs and real-Mac verification

**Files:**
- Modify: `README.md`, `docs/superpowers/specs/2026-09-27-apple-music-agent-design.md`

- [ ] **Step 1: README.** Replace the sentence "Keep `music-agent run` going, or run `music-agent snapshot` regularly (Phase 2 adds a background service)." with:

```markdown
Turn on background recording once with `uv run music-agent service install`: launchd then takes a snapshot every 10 minutes and at login, even when nothing else is running. If the Mac sleeps, the missed run happens when it wakes. `music-agent service status` shows whether it's on, and `service uninstall` turns it off. If you set up Telegram, run `service install` again to also start the bot at login. On a laptop it keeps the Mac awake while plugged in.
```

Also add `service install|uninstall|status` to the "Other commands" line.

- [ ] **Step 2: Spec.** In "Open items to verify during implementation", replace item 2 with:

```markdown
2. ~~launchd-run `osascript` gets the Automation permission for Music~~ Verified 2026-10-18: a launchd-started `music-agent snapshot` read the library with exit 0 and no prompt on the author's Mac. Other Macs may see a one-time prompt; `doctor` must detect and explain a denial. `~/Library/LaunchAgents` may not exist and must be created.
```

- [ ] **Step 3: Commit the docs**

```bash
git add README.md docs/superpowers/specs/2026-09-27-apple-music-agent-design.md
git commit -m "docs: document the background service"
```

- [ ] **Step 4: Install for real and verify** (user-visible: this turns background recording on)

Run: `uv run music-agent service install && uv run music-agent service status`
Expected: "Background snapshots installed…", then "Background snapshots: on (every 10 minutes)" and "Telegram bot service: not installed" (no bot token yet).

Then confirm the job ran: `launchctl print gui/$(id -u)/io.music-agent.snapshot | grep -E "state =|last exit code"` should show `last exit code = 0`, and `uv run music-agent status` should show a "Last snapshot" time within the last few minutes.

- [ ] **Step 5: Verify the 10-minute schedule.** Check `uv run music-agent status` again at least 11 minutes after Step 4 (use a background wait, not a foreground sleep). Expected: the "Last snapshot" time has moved forward with no manual command.

- [ ] **Step 6: Final checks.** `uv run pytest && uv run pytest -m mac && uv run ruff check`. Check each exit code directly; don't pipe through `tail`.
