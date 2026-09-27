"""Optional Telegram front end, plus the in-process snapshot scheduler used by `run`."""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
from collections.abc import Callable

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from . import music
from .config import Config, get_secret
from .snapshot import run_snapshot
from .store import Store

log = logging.getLogger(__name__)

TELEGRAM_LIMIT = 4096
HELP_TEXT = (
    'Ask me about your Apple Music listening, e.g. "how many songs did I play today?" or '
    '"make a playlist of the rock I played this week".\n'
    "/new starts a fresh conversation, /status shows recording status and spend."
)
NO_TELEGRAM_MSG = (
    "Telegram is optional and not set up. To use it: create a bot with @BotFather, then run\n"
    "  uv run keyring set music-agent telegram_bot_token\n"
    "and start `music-agent run` again. You can always use `music-agent chat` instead."
)


def split_message(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    parts: list[str] = []
    remaining = text
    while len(remaining) > limit:
        cut = remaining.rfind("\n", 0, limit + 1)
        if cut <= 0:
            parts.append(remaining[:limit])
            remaining = remaining[limit:]
        else:
            parts.append(remaining[:cut])
            remaining = remaining[cut + 1 :]
    parts.append(remaining)
    return [p for p in parts if p]


class BotLogic:
    def __init__(self, agent, allowed_user_id: int | None, status_text: Callable[[], str]):
        self.agent = agent
        self.allowed_user_id = allowed_user_id
        self.status_text = status_text

    def is_allowed(self, user_id: int) -> bool:
        return self.allowed_user_id is not None and user_id == self.allowed_user_id

    def handle(self, user_id: int, chat_id: int, text: str) -> list[str] | None:
        if self.allowed_user_id is None:
            return [
                (
                    f"Your Telegram user ID is {user_id}. To let this account use the bot, add\n"
                    f"telegram_allowed_user_id = {user_id}\n"
                    "to config.toml and restart `music-agent run`."
                )
            ]
        if not self.is_allowed(user_id):
            log.warning("ignored Telegram message from user %s", user_id)
            return None
        conversation = f"tg:{chat_id}"
        stripped = text.strip()
        command = stripped.split()[0].split("@")[0].lower() if stripped.startswith("/") else None
        if command in ("/start", "/help"):
            return [HELP_TEXT]
        if command == "/new":
            self.agent.reset(conversation)
            return ["Started a new conversation."]
        if command == "/status":
            return split_message(self.status_text())
        return split_message(self.agent.respond(conversation, text))


def build_application(token: str, logic: BotLogic) -> Application:
    app = Application.builder().token(token).build()

    async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message is None or update.effective_user is None or update.effective_chat is None:
            return
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        if logic.is_allowed(user_id):
            await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
        replies = await asyncio.to_thread(logic.handle, user_id, chat_id, update.message.text or "")
        for reply in replies or []:
            await update.message.reply_text(reply)

    app.add_handler(MessageHandler(filters.TEXT, on_message))
    return app


class SnapshotScheduler:
    """Runs `job` now and then every `interval_seconds` on a daemon thread until stop()."""

    def __init__(self, interval_seconds: float, job: Callable[[], object]):
        self.interval = interval_seconds
        self.job = job
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="snapshot-scheduler", daemon=True)

    def _loop(self) -> None:
        while True:
            try:
                self.job()
            except Exception:
                log.exception("scheduled snapshot failed")
            if self._stop.wait(self.interval):
                return

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)


def run_bot(config: Config, store: Store, no_scheduler: bool = False) -> int:
    from .cli import _require_key, _status_text, build_agent

    token = get_secret("telegram_bot_token")
    if not token:
        print(NO_TELEGRAM_MSG, file=sys.stderr)
        return 2
    key = _require_key()
    if not key:
        return 2
    logic = BotLogic(
        build_agent(config, store, key),
        config.telegram_allowed_user_id,
        lambda: _status_text(config, store),
    )
    scheduler = None
    if not no_scheduler:
        scheduler = SnapshotScheduler(
            config.snapshot_interval_minutes * 60, lambda: run_snapshot(store, music.read_library)
        )
        scheduler.start()
    if config.telegram_allowed_user_id is None:
        print("No telegram_allowed_user_id set: the bot will only reply with each sender's user ID.")
    print("music-agent bot running. Press Ctrl-C to stop.")
    try:
        build_application(token, logic).run_polling()
    finally:
        if scheduler:
            scheduler.stop()
    return 0
