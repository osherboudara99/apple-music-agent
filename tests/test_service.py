import os
import plistlib
import subprocess
from pathlib import Path

import pytest

from music_agent import service
from music_agent.config import Config
from music_agent.service import BOT_LABEL, SNAPSHOT_LABEL

EXE = Path("/Users/me/.local/bin/music-agent")
LOGS = Path("/Users/me/Library/Application Support/music-agent/logs")


def completed(stdout="", returncode=0, stderr=""):
    return lambda args: subprocess.CompletedProcess(args, returncode, stdout, stderr)


def test_snapshot_job(monkeypatch):
    monkeypatch.delenv("MUSIC_AGENT_HOME", raising=False)
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
    assert job["ProgramArguments"] == [
        "/usr/bin/caffeinate", "-s", str(EXE), "run", "--no-scheduler",
    ]
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
    assert service.plist_path("io.music-agent.bot", tmp_path) == (
        tmp_path / "io.music-agent.bot.plist"
    )
    assert service.agents_dir() == Path.home() / "Library" / "LaunchAgents"


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
        Config(timezone="UTC", snapshot_interval_minutes=30), False, fake, tmp_path, EXE,
        battery=False,
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
            Config(timezone="UTC"), False, FakeLaunchctl(bootstrap_rc=5), tmp_path, EXE,
            battery=False,
        )


def test_install_leaves_no_temp_files(tmp_path):
    agents = tmp_path / "agents"  # tmp_path also holds the isolated data folder
    service.install(Config(timezone="UTC"), False, FakeLaunchctl(), agents, EXE, battery=False)
    assert sorted(p.name for p in agents.iterdir()) == [f"{SNAPSHOT_LABEL}.plist"]


def test_uninstall(tmp_path):
    fake = FakeLaunchctl()
    agents = tmp_path / "agents"
    service.install(Config(timezone="UTC"), True, fake, agents, EXE, battery=False)
    assert service.uninstall(fake, agents) == [SNAPSHOT_LABEL, BOT_LABEL]
    assert fake.loaded == set() and list(agents.iterdir()) == []


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
