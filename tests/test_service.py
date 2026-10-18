import subprocess
from pathlib import Path

import pytest

from music_agent import service
from music_agent.config import Config

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
