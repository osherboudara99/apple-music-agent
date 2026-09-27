import time

from music_agent import bot
from music_agent.config import Config
from music_agent.store import Store


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
    assert bot.split_message(text) == ["a" * 3000, "b" * 3000]


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
    bot_logic = logic(agent)
    assert bot_logic.handle(42, 100, "/new") == ["Started a new conversation."]
    assert agent.calls == [("reset", "tg:100")]
    assert bot_logic.handle(42, 100, "/status") == ["status text"]
    assert "Ask me" in bot_logic.handle(42, 100, "/start")[0]
    assert "Ask me" in bot_logic.handle(42, 100, "/help@my_music_bot")[0]


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
    code = bot.run_bot(Config(timezone="UTC"), Store(isolated_home / "plays.db"))
    assert code == 2
    assert "Telegram is optional" in capsys.readouterr().err
