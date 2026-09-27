"""`music-agent` command-line entry point."""

from __future__ import annotations

import argparse
import logging
import sys

import anthropic

from . import music, queries
from .agent import Agent, month_start
from .config import Config, ConfigError, db_path, get_secret, load_config, log_dir
from .models import utcnow
from .snapshot import maybe_snapshot, run_snapshot
from .store import Store
from .tools import Toolbox, ToolContext

NOT_MAC_MSG = "music-agent only works on macOS (it reads your library from Music.app)."
NO_KEY_MSG = (
    "No Anthropic API key found. Set one with:\n"
    "  uv run keyring set music-agent anthropic_api_key\n"
    "or export MUSIC_AGENT_ANTHROPIC_API_KEY. (A generic ANTHROPIC_API_KEY is ignored on purpose.)"
)
CLI_CHAT_ID = "cli"


def setup_logging(verbose: bool = False) -> None:
    log_dir().mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    file_handler = logging.FileHandler(log_dir() / "music-agent.log")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.INFO if verbose else logging.WARNING)
    console.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    root.handlers[:] = [file_handler, console]


def build_agent(config: Config, store: Store, api_key: str) -> Agent:
    ctx = ToolContext(
        store=store,
        config=config,
        clock=utcnow,
        refresh=lambda: maybe_snapshot(store, music.read_library),
        create_playlist=lambda **kwargs: music.create_playlist(**kwargs),
    )
    return Agent(anthropic.Anthropic(api_key=api_key), store, config, Toolbox(ctx))


def format_status(summary: dict, spent: float, config: Config) -> str:
    tz = config.tz

    def when(dt):
        return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M")

    last = summary["last_snapshot"]
    if last is None:
        return "No snapshots yet. Run `music-agent snapshot` to record a baseline."
    lines = [f"Tracks in library: {summary['track_count']:,}"]
    if summary["install_at"]:
        lines.append(f"Recording plays since: {when(summary['install_at'])}")
    if last["error"]:
        lines.append(f"Last snapshot: {when(last['at'])} FAILED: {last['error']}")
    else:
        lines.append(f"Last snapshot: {when(last['at'])} (+{last['events_added']} plays)")
    lines.append(f"Plays recorded: {summary['plays_recorded']:,}")
    lag = summary["sync_lag"]
    if lag:
        lines.append(
            f"Sync lag (incl. polling): median {lag['median_min']} min, p95 {lag['p95_min']} min "
            f"({lag['samples']} plays, last 30 days)"
        )
    lines.append(f"API spend this month: ${spent:.2f} of ${config.monthly_budget_usd:.2f}")
    return "\n".join(lines)


def _status_text(config: Config, store: Store) -> str:
    now = utcnow()
    spent = store.spend_since(month_start(now, config.tz))
    return format_status(queries.status_summary(store, now), spent, config)


def _cmd_snapshot(store: Store) -> int:
    result = run_snapshot(store, music.read_library)
    if result.error:
        print(f"Snapshot failed: {result.error}", file=sys.stderr)
        return 1
    if result.first_run:
        print(
            f"Recorded a baseline of {result.track_count:,} tracks. "
            "Plays are counted from now on."
        )
    else:
        print(f"Snapshot of {result.track_count:,} tracks: {result.events_added} new plays.")
    return 0


def _require_key() -> str | None:
    key = get_secret("anthropic_api_key")
    if not key:
        print(NO_KEY_MSG, file=sys.stderr)
    return key


def _cmd_chat(agent: Agent) -> int:
    print("music-agent chat. Ask about your listening. /new starts over, /exit quits.")
    while True:
        try:
            line = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line in ("/exit", "/quit"):
            return 0
        if line == "/new":
            agent.reset(CLI_CHAT_ID)
            print("Started a new conversation.")
            continue
        print(agent.respond(CLI_CHAT_ID, line))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="music-agent",
        description="Ask questions about your Apple Music listening and build playlists (macOS).",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="show info logs on stderr")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("chat", help="interactive conversation in the terminal")
    ask = sub.add_parser("ask", help="ask one question")
    ask.add_argument("question", nargs="+")
    sub.add_parser("snapshot", help="record plays from Music.app now")
    sub.add_parser("status", help="show recording status and spend")
    run = sub.add_parser("run", help="run the Telegram bot in the foreground")
    run.add_argument(
        "--no-scheduler", action="store_true", help="don't take snapshots in this process"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if sys.platform != "darwin":
        print(NOT_MAC_MSG, file=sys.stderr)
        return 2
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2
    setup_logging(args.verbose)
    store = Store(db_path())

    if args.command == "snapshot":
        return _cmd_snapshot(store)
    if args.command == "status":
        print(_status_text(config, store))
        return 0
    if args.command in ("chat", "ask"):
        key = _require_key()
        if not key:
            return 2
        agent = build_agent(config, store, key)
        if args.command == "ask":
            print(agent.respond(CLI_CHAT_ID, " ".join(args.question)))
            return 0
        return _cmd_chat(agent)
    if args.command == "run":
        from .bot import run_bot  # imported lazily: only `run` needs python-telegram-bot

        return run_bot(config, store, no_scheduler=args.no_scheduler)
    return 2
