from pathlib import Path

import pytest

from music_agent import cli, music, service
from music_agent.config import Config, set_secret
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
    err = capsys.readouterr().err
    assert "keyring set music-agent anthropic_api_key" in err
    assert "MUSIC_AGENT_ANTHROPIC_API_KEY" in err


def test_ask_is_one_shot(stub_agent, capsys):
    set_secret("anthropic_api_key", "sk-test")
    assert cli.main(["ask", "how", "many", "songs?"]) == 0
    assert cli.main(["ask", "again"]) == 0
    assert capsys.readouterr().out.split("\n")[:2] == ["answer to how many songs?", "answer to again"]
    (id1, q1), (reset1, _), (id2, _), (reset2, _) = stub_agent.calls
    assert q1 == "how many songs?"
    assert id1.startswith("ask-") and id1 != id2 and "cli" not in (id1, id2)
    assert (reset1, reset2) == (id1, id2)  # no history left behind


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
    summary = {
        "install_at": dt("2026-09-20T00:00:00"),
        "track_count": 31699,
        "plays_recorded": 14,
        "last_snapshot": {
            "at": dt("2026-09-27T19:00:00"), "track_count": 31699, "events_added": 2, "error": None,
        },
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


def test_logging_hides_http_urls_and_rotates(isolated_home):
    import logging
    import logging.handlers

    root = logging.getLogger()
    saved = root.handlers[:], root.level
    try:
        cli.setup_logging()
        logging.getLogger("httpx").info("POST https://api.telegram.org/bot123:SECRET/getUpdates")
        logging.getLogger("httpx2").info("POST https://api.anthropic.com/v1/messages")
        logging.getLogger("music_agent.test").info("visible line")
        for handler in root.handlers:
            handler.flush()
        text = (isolated_home / "logs" / "music-agent.log").read_text()
        assert "SECRET" not in text and "api.anthropic.com" not in text
        assert "visible line" in text
        assert any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers)
    finally:
        root.handlers[:], root.level = saved[0], saved[1]


class ExplodingAgent(StubAgent):
    def __init__(self, error):
        super().__init__()
        self.error = error

    def respond(self, chat_id, text):
        self.calls.append((chat_id, text))
        if text == "boom":
            raise self.error
        return f"answer to {text}"


def test_chat_survives_unexpected_errors_and_ctrl_c(monkeypatch, capsys):
    set_secret("anthropic_api_key", "sk-test")
    agent = ExplodingAgent(RuntimeError("database is locked"))
    monkeypatch.setattr(cli, "build_agent", lambda config, store, api_key: agent)
    inputs = iter(["boom", "hello", "/exit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    assert cli.main(["chat"]) == 0
    out = capsys.readouterr().out
    assert "Something went wrong" in out and "answer to hello" in out

    agent = ExplodingAgent(KeyboardInterrupt())
    monkeypatch.setattr(cli, "build_agent", lambda config, store, api_key: agent)
    inputs = iter(["boom", "hello", "/exit"])
    assert cli.main(["chat"]) == 0
    assert "(stopped)" in capsys.readouterr().out


def test_ask_unexpected_error_exit_code(monkeypatch, capsys):
    set_secret("anthropic_api_key", "sk-test")
    agent = ExplodingAgent(RuntimeError("database is locked"))
    monkeypatch.setattr(cli, "build_agent", lambda config, store, api_key: agent)
    assert cli.main(["ask", "boom"]) == 1
    assert "Something went wrong" in capsys.readouterr().err


def test_build_agent_ignores_shell_base_url(monkeypatch, isolated_home):
    from music_agent.store import Store

    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://proxy.example.com")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "someone-elses-token")
    agent = cli.build_agent(Config(timezone="UTC"), Store(isolated_home / "p.db"), "sk-mine")
    client = agent.client
    assert str(client.base_url).startswith("https://api.anthropic.com")
    assert client.api_key == "sk-mine" and client.auth_token is None


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
    set_secret("anthropic_api_key", "sk-test")
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


def test_format_service_status_missing_program():
    cfg = Config(timezone="UTC")
    text = cli.format_service_status(
        [service.JobStatus(service.SNAPSHOT_LABEL, True, True, False, 78, "/old/music-agent", False),
         job(service.BOT_LABEL, False, False, False, None)], cfg,
    )
    assert "/old/music-agent" in text and "no longer exists" in text
    assert "music-agent service install" in text


def test_service_install_uses_keychain_secrets_only(fake_service, monkeypatch, capsys):
    monkeypatch.setenv("MUSIC_AGENT_TELEGRAM_BOT_TOKEN", "123:from-shell")
    set_secret("anthropic_api_key", "sk-test")
    assert cli.main(["service", "install"]) == 0
    assert fake_service["install"] is False  # the background bot can't see shell variables
    assert "shell" in capsys.readouterr().out.lower()


def test_service_install_needs_both_keys_in_keychain_for_the_bot(fake_service):
    set_secret("telegram_bot_token", "123:abc")  # no Anthropic key stored
    assert cli.main(["service", "install"]) == 0
    assert fake_service["install"] is False


def test_status_shows_installed_interval_and_mismatch():
    cfg = Config(timezone="UTC")  # 10 minutes in config
    snap = service.JobStatus(service.SNAPSHOT_LABEL, True, True, False, 0, "/x", True, 1800)
    text = cli.format_service_status([snap, job(service.BOT_LABEL, False, False, False, None)], cfg)
    assert "every 30 minutes" in text and "config says 10" in text
    assert "music-agent service install" in text


def test_status_shows_full_log_paths(isolated_home):
    from music_agent.config import log_dir

    failing = cli.format_service_status(
        [job(service.SNAPSHOT_LABEL, last_exit=1), job(service.BOT_LABEL, False, False, False, None)],
        Config(timezone="UTC"),
    )
    assert str(log_dir() / "snapshot.err.log") in failing


def test_status_when_launchctl_fails():
    broken = service.JobStatus(service.SNAPSHOT_LABEL, True, False, False, None, "/x", True, None,
                               "timed out")
    text = cli.format_service_status(
        [broken, service.JobStatus(service.BOT_LABEL, False, False, False, None, None, True, None,
                                   "timed out")],
        Config(timezone="UTC"),
    )
    assert "state unknown" in text and "timed out" in text


def test_status_before_first_snapshot_still_shows_service(capsys):
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "No snapshots yet" in out and "Background snapshots: off" in out


def test_job_output_logs_are_trimmed(monkeypatch, isolated_home):
    from music_agent.config import log_dir

    log_dir().mkdir(parents=True, exist_ok=True)
    big = log_dir() / "snapshot.out.log"
    big.write_text("old line\n" * 200_000 + "LAST LINE\n")  # ~1.8 MB
    monkeypatch.setattr(music, "read_library", lambda: [make_track("A", played_count=1)])
    assert cli.main(["snapshot"]) == 0
    text = big.read_text()
    assert big.stat().st_size <= cli.JOB_LOG_KEEP_BYTES + 200
    assert text.rstrip().endswith("LAST LINE") and text.startswith("old line")


def _break_config(home):
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text('timezone = "UTC"\nmodle = "typo"\n')


def test_service_uninstall_works_even_with_a_broken_config(fake_service, isolated_home, capsys):
    _break_config(isolated_home)
    assert cli.main(["service", "uninstall"]) == 0  # the off switch must always work
    assert "Removed" in capsys.readouterr().out


def test_service_job_with_broken_config_exits_cleanly(isolated_home, capsys):
    _break_config(isolated_home)
    assert cli.main(["run", "--no-scheduler", "--service"]) == 0  # KeepAlive won't loop
    assert "modle" in capsys.readouterr().err


def test_foreground_run_with_broken_config_still_fails(isolated_home):
    _break_config(isolated_home)
    assert cli.main(["run"]) == 2
