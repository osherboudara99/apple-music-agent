"""The agent loop: Claude + our tools, with per-chat history, usage tracking and a budget."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

import anthropic

from .config import Config
from .models import utcnow
from .store import Store

log = logging.getLogger(__name__)

# (input, output) USD per million tokens. Unknown models are priced conservatively.
PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-opus-5": (5.0, 25.0),
}
FALLBACK_PRICE = (5.0, 25.0)

MAX_TOKENS = 16000
MAX_TOOL_ROUNDS = 10
MAX_HISTORY_MESSAGES = 40
IDLE_RESET = timedelta(minutes=30)

API_ERROR_MSG = "Claude API unavailable, try again shortly."
TOO_MANY_STEPS_MSG = (
    "I couldn't finish that in a reasonable number of steps. Try a narrower question."
)
REFUSAL_MSG = "Claude declined to answer that one."
TOO_LARGE_MSG = (
    "That request was too large to finish in one go. Try a smaller one (e.g. fewer tracks)."
)


class ToolRunner(Protocol):
    def definitions(self) -> list[dict]: ...
    def run(self, name: str, args: dict) -> dict: ...


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = PRICES_PER_MTOK.get(model, FALLBACK_PRICE)
    return input_tokens * price_in / 1_000_000 + output_tokens * price_out / 1_000_000


def month_start(now: datetime, tz: ZoneInfo) -> datetime:
    local = now.astimezone(tz)
    start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(now.tzinfo)


def system_prompt(config: Config, now: datetime) -> str:
    local = now.astimezone(config.tz)
    return f"""You are music-agent, a concise assistant for the user's Apple Music library on their Mac.
You answer questions about their listening and create playlists using the tools.
Replies are read on a phone or in a terminal: keep them short, plain text, no markdown tables.

Current local time: {local:%A %Y-%m-%d %H:%M} ({config.timezone}).
Time windows: "today" = since local midnight; "past week" / "last 7 days" = past_week (rolling
7x24h); "this week" = since Monday 00:00; "this month"; "this year" = since Jan 1. For other
ranges pass start/end dates.

Rules:
- Always use the tools for numbers, songs and playlists. Never guess or invent them.
- "How many songs" means distinct_tracks unless the user asks about plays or repeats.
- If plays_is_lower_bound is true, say plays are only fully counted since plays_counted_since
  (e.g. "at least 57 plays").
- If plays_near_boundary > 0, say about that many of the counted plays may be from just before
  the period (their exact time is unknown).
- For genre requests like "rock", use genre_family and mention which genres were included.
- Create playlists right away when asked. Name each playlist after what's actually in it
  (e.g. "Rock I Played This Week"), and give a one-sentence description of its contents.
  Reply with the playlist name, the number of tracks and the first few tracks. Playlists are
  created in the "{config.playlist_folder}" folder.
- Playlists can't be made public from here: if asked, tell the user to turn on "Show on My
  Profile" for the playlist in the Music app.
- Themed playlists: get candidates with played_tracks, then pick the tracks that fit the theme
  from your knowledge of the songs. If fewer than 5 fit, say so and offer to widen the window
  (past month, or the whole library via search_library) instead of padding.
- If a tool returns an error or a warning, tell the user plainly.
- You cannot edit or delete existing playlists or tracks."""


def _block_param(block) -> dict | None:
    if block.type == "text":
        return {"type": "text", "text": block.text}
    if block.type == "tool_use":
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    return None


def _is_plain_user(message: dict) -> bool:
    return message["role"] == "user" and isinstance(message["content"], str)


class Agent:
    def __init__(
        self,
        client: anthropic.Anthropic,
        store: Store,
        config: Config,
        toolbox: ToolRunner,
        clock: Callable[[], datetime] = utcnow,
    ):
        self.client = client
        self.store = store
        self.config = config
        self.toolbox = toolbox
        self.clock = clock

    def reset(self, chat_id: str) -> None:
        self.store.clear_messages(chat_id)

    def _history(self, chat_id: str, now: datetime) -> list[dict]:
        rows = self.store.load_messages(chat_id)
        if not rows:
            return []
        if now - rows[-1][1] > IDLE_RESET:
            self.store.clear_messages(chat_id)
            return []
        messages = [m for m, _ in rows][-MAX_HISTORY_MESSAGES:]
        while messages and not _is_plain_user(messages[0]):
            messages = messages[1:]
        return messages

    def _over_budget(self, now: datetime) -> str | None:
        spent = self.store.spend_since(month_start(now, self.config.tz))
        if spent < self.config.monthly_budget_usd:
            return None
        return (
            f"Monthly budget of ${self.config.monthly_budget_usd:.2f} reached "
            f"(spent ${spent:.2f}). Raise monthly_budget_usd in config.toml or wait "
            "until next month."
        )

    def _record_usage(self, response, now: datetime) -> None:
        usage = response.usage
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        self.store.record_usage(
            now,
            self.config.model,
            usage.input_tokens,
            usage.output_tokens,
            cache_read,
            cost_usd(self.config.model, usage.input_tokens, usage.output_tokens),
        )

    def respond(self, chat_id: str, text: str) -> str:
        now = self.clock()
        budget_msg = self._over_budget(now)
        if budget_msg:
            return budget_msg
        history = self._history(chat_id, now)
        new: list[dict] = [{"role": "user", "content": text}]
        created: list[dict] = []  # playlists made this turn, reported if the turn fails later
        try:
            for round_number in range(MAX_TOOL_ROUNDS):
                if round_number and (budget_msg := self._over_budget(now)):
                    # Recheck between calls: one question must not run far past the budget.
                    return self._failed_turn(chat_id, text, created, now, budget_msg)
                response = self.client.messages.create(
                    model=self.config.model,
                    max_tokens=MAX_TOKENS,
                    system=system_prompt(self.config, now),
                    tools=self.toolbox.definitions(),
                    messages=history + new,
                )
                self._record_usage(response, now)
                blocks = [b for b in (_block_param(x) for x in response.content) if b is not None]
                if response.stop_reason != "tool_use":
                    # A tool_use here was cut off (e.g. max_tokens) and never runs; saving it
                    # without a tool_result would make every later request fail.
                    blocks = [b for b in blocks if b["type"] != "tool_use"]
                if blocks:  # the API rejects assistant turns with empty content
                    new.append({"role": "assistant", "content": blocks})
                if response.stop_reason != "tool_use":
                    self.store.append_messages(chat_id, new, now)
                    return self._final_text(response)
                results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue
                    output = self.toolbox.run(block.name, block.input)
                    if block.name == "create_playlist" and "error" not in output:
                        created.append(output)
                    result = {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(output, ensure_ascii=False, default=str),
                    }
                    if "error" in output:
                        result["is_error"] = True
                    results.append(result)
                new.append({"role": "user", "content": results})
            return self._failed_turn(chat_id, text, created, now, TOO_MANY_STEPS_MSG)
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
            request_id = None
            if isinstance(exc, anthropic.APIStatusError):
                request_id = exc.response.headers.get("request-id")
            log.warning("Claude API error (request-id %s): %s", request_id, exc)
            return self._failed_turn(chat_id, text, created, now, API_ERROR_MSG)

    def _failed_turn(
        self, chat_id: str, text: str, created: list[dict], now: datetime, message: str
    ) -> str:
        """Save nothing half-finished; but if playlists were made, say so and remember it."""
        if not created:
            return message
        made = ", ".join(f'"{p["name"]}" ({p.get("track_count", "?")} tracks)' for p in created)
        note = f"Before that happened I created the playlist {made}."
        self.store.append_messages(
            chat_id,
            [
                {"role": "user", "content": text},
                {"role": "assistant", "content": [{"type": "text", "text": note}]},
            ],
            now,
        )
        return f"{message} {note}"

    @staticmethod
    def _final_text(response) -> str:
        text = "\n".join(b.text for b in response.content if b.type == "text").strip()
        if response.stop_reason == "refusal" and not text:
            return REFUSAL_MSG
        if response.stop_reason == "max_tokens":
            if any(b.type == "tool_use" for b in response.content):
                return TOO_LARGE_MSG
            return f"{text} (answer cut off)"
        return text or "(no answer)"
