import pytest

from music_agent import cli, music
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
