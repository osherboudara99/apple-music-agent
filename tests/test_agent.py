import json
from datetime import timedelta
from zoneinfo import ZoneInfo

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
        return [
            {
                "name": "listening_stats",
                "description": "d",
                "input_schema": {"type": "object", "properties": {}},
            }
        ]

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
    assert ag.month_start(NOW, ZoneInfo("America/Los_Angeles")) == dt("2026-09-01T07:00:00")


def test_system_prompt_mentions_time_and_folder():
    prompt = ag.system_prompt(Config(timezone="America/Los_Angeles"), NOW)
    assert "2026-09-27 12:00" in prompt and "Sunday" in prompt
    assert '"Music Agent"' in prompt


def test_plain_answer_persists_history_and_usage(store):
    agent, client = make_agent(store, [msg([text("You played 4 songs today.")])])
    assert agent.respond("cli", "how many songs today?") == "You played 4 songs today."
    call = client.messages.calls[0]
    assert call["model"] == "claude-haiku-4-5" and call["max_tokens"] == ag.MAX_TOKENS
    assert call["messages"] == [{"role": "user", "content": "how many songs today?"}]
    assert [m["role"] for m, _ in store.load_messages("cli")] == ["user", "assistant"]
    assert store.spend_since(dt("2026-09-01T00:00:00")) == pytest.approx(
        ag.cost_usd("claude-haiku-4-5", 1000, 100)
    )


def test_tool_loop(store):
    toolbox = StubToolbox({"listening_stats": {"distinct_tracks": 4}})
    agent, client = make_agent(
        store,
        [
            msg(
                [text("Checking."), tool_use("toolu_1", "listening_stats", {"period": "today"})],
                "tool_use",
            ),
            msg([text("4 songs.")]),
        ],
        toolbox,
    )
    assert agent.respond("cli", "songs today?") == "4 songs."
    assert toolbox.calls == [("listening_stats", {"period": "today"})]
    second = client.messages.calls[1]["messages"]
    assert second[1]["content"][1] == {
        "type": "tool_use",
        "id": "toolu_1",
        "name": "listening_stats",
        "input": {"period": "today"},
    }
    result = second[2]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "toolu_1"
    assert json.loads(result["content"]) == {"distinct_tracks": 4}
    assert "is_error" not in result
    assert len(store.load_messages("cli")) == 4


def test_tool_error_is_flagged(store):
    toolbox = StubToolbox({"listening_stats": {"error": "Unknown period"}})
    agent, client = make_agent(
        store,
        [
            msg([tool_use("toolu_1", "listening_stats", {"period": "x"})], "tool_use"),
            msg([text("Sorry.")]),
        ],
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
    error = anthropic.APIConnectionError(
        request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    )
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
    looping = [
        msg([tool_use(f"toolu_{i}", "listening_stats", {})], "tool_use")
        for i in range(ag.MAX_TOOL_ROUNDS)
    ]
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


def test_truncated_tool_call_is_not_saved_and_chat_keeps_working(store):
    toolbox = StubToolbox()
    agent, client = make_agent(
        store,
        [
            msg([text("Creating it."), tool_use("toolu_1", "listening_stats", {"period": "t"})],
                "max_tokens"),
            msg([text("fine")]),
        ],
        toolbox,
    )
    reply = agent.respond("cli", "huge playlist please")
    assert "too large" in reply
    assert toolbox.calls == []  # a cut-off tool call is never executed
    agent.respond("cli", "next")
    sent = client.messages.calls[1]["messages"]
    tool_uses = [
        b for m in sent if m["role"] == "assistant" for b in m["content"] if b["type"] == "tool_use"
    ]
    assert tool_uses == []


def test_max_tokens_is_generous():
    assert ag.MAX_TOKENS >= 16000
