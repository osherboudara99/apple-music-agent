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
