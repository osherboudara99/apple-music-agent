"""`music-agent` command-line entry point."""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import sys
import uuid
from datetime import timedelta

import anthropic

from . import music, queries, service
from .agent import Agent, month_start
from .config import (
    Config,
    ConfigError,
    db_path,
    get_keychain_secret,
    get_secret,
    load_config,
    log_dir,
)
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
JOB_LOG_NAMES = ("snapshot.out.log", "snapshot.err.log", "bot.out.log", "bot.err.log")
JOB_LOG_MAX_BYTES = 1_000_000
JOB_LOG_KEEP_BYTES = 200_000
ANTHROPIC_BASE_URL = "https://api.anthropic.com"
UNEXPECTED_MSG = "Something went wrong on my side; details are in the log. Try again."


def setup_logging(verbose: bool = False) -> None:
    log_dir().mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir() / "music-agent.log", maxBytes=1_000_000, backupCount=3
    )
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.INFO if verbose else logging.WARNING)
    console.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    root.handlers[:] = [file_handler, console]
    # HTTP clients log full request URLs at INFO, and Telegram URLs contain the bot token.
    for noisy in ("httpx", "httpx2", "httpcore", "telegram", "apscheduler"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def trim_job_logs() -> None:
    """launchd appends job output to these files forever; keep only the recent tail."""
    for name in JOB_LOG_NAMES:
        path = log_dir() / name
        try:
            if path.stat().st_size <= JOB_LOG_MAX_BYTES:
                continue
            with path.open("rb") as f:
                f.seek(-JOB_LOG_KEEP_BYTES, 2)
                tail = f.read()
            newline = tail.find(b"\n")
            path.write_bytes(tail[newline + 1 :] if newline != -1 else tail)
        except OSError:
            continue


def build_agent(config: Config, store: Store, api_key: str) -> Agent:
    ctx = ToolContext(
        store=store,
        config=config,
        clock=utcnow,
        refresh=lambda: maybe_snapshot(store, music.read_library),
        create_playlist=lambda **kwargs: music.create_playlist(**kwargs),
    )
    # Explicit key and base_url: a shell's ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN (e.g. a work
    # proxy) must never receive or replace this key.
    client = anthropic.Anthropic(api_key=api_key, base_url=ANTHROPIC_BASE_URL)
    return Agent(client, store, config, Toolbox(ctx))


def format_status(
    summary: dict, spent: float, config: Config, services: list | None = None
) -> str:
    tz = config.tz

    def when(dt):
        return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M")

    last = summary["last_snapshot"]
    if last is None:
        text = "No snapshots yet. Run `music-agent snapshot` to record a baseline."
        if services is not None:
            text += "\n" + format_service_status(services, config)
        return text
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
    if services is not None:
        lines.append(format_service_status(services, config))
    return "\n".join(lines)


def format_service_status(statuses: list[service.JobStatus], config: Config) -> str:
    by_label = {s.label: s for s in statuses}
    logs = log_dir()
    snap = by_label[service.SNAPSHOT_LABEL]
    if snap.error:
        snap_line = f"Background snapshots: state unknown (launchctl failed: {snap.error})"
    elif snap.installed and not snap.program_exists:
        snap_line = (
            f"Background snapshots: broken. The job runs {snap.program}, which no longer "
            "exists; run `music-agent service install` again from where music-agent is now."
        )
    elif not (snap.installed and snap.loaded):
        snap_line = (
            "Background snapshots: off (plays are only recorded while a command runs). "
            "Turn on with `music-agent service install`."
        )
    elif snap.last_exit not in (None, 0):
        snap_line = (
            f"Background snapshots: on, but the last run failed (exit {snap.last_exit}); "
            f"see {logs / 'snapshot.err.log'}"
        )
    else:
        minutes = (snap.interval_s // 60) if snap.interval_s else config.snapshot_interval_minutes
        snap_line = f"Background snapshots: on (every {minutes} minutes)"
        if minutes != config.snapshot_interval_minutes:
            snap_line += (
                f"; config says {config.snapshot_interval_minutes}, run "
                "`music-agent service install` to apply it"
            )
    bot = by_label[service.BOT_LABEL]
    if bot.error:
        bot_line = f"Telegram bot service: state unknown (launchctl failed: {bot.error})"
    elif not bot.installed:
        bot_line = "Telegram bot service: not installed"
    elif not bot.program_exists:
        bot_line = f"Telegram bot service: broken ({bot.program} no longer exists)"
    elif bot.running:
        bot_line = "Telegram bot service: running"
    else:
        bot_line = (
            f"Telegram bot service: installed but not running; see {logs / 'bot.err.log'}"
        )
    return f"{snap_line}\n{bot_line}"


def _cmd_service(action: str, config: Config) -> int:
    try:
        if action == "install":
            # The background bot reads the Keychain only (launchd jobs don't see shell variables).
            token = get_keychain_secret("telegram_bot_token")
            key = get_keychain_secret("anthropic_api_key")
            telegram = bool(token and key)
            if not token and get_secret("telegram_bot_token"):
                print(
                    "Your Telegram token is only set in your shell; the background bot can't "
                    "see it. Store it with: uv run keyring set music-agent telegram_bot_token"
                )
            elif token and not key:
                print(
                    "The background bot also needs your Anthropic key in the Keychain: "
                    "uv run keyring set music-agent anthropic_api_key"
                )
            result = service.install(config, telegram)
            print(
                f"Background snapshots installed: every {config.snapshot_interval_minutes} "
                "minutes, and at login."
            )
            if service.BOT_LABEL in result.installed:
                awake = " (keeps the Mac awake while plugged in)" if result.keep_awake else ""
                print(f"Telegram bot installed: starts at login and restarts if it stops{awake}.")
            else:
                print("Telegram bot not installed (no bot token). Re-run after setting one up.")
            if service.BOT_LABEL in result.removed:
                print("Removed the old Telegram bot job.")
            print(f"Logs: {result.logs}")
            return 0
        if action == "uninstall":
            removed = service.uninstall()
            print("Removed: " + ", ".join(removed) if removed else "Nothing was installed.")
            return 0
        print(format_service_status(service.status(), config))
        return 0
    except service.ServiceError as exc:
        print(f"Service error: {exc}", file=sys.stderr)
        return 1


def _status_text(config: Config, store: Store) -> str:
    now = utcnow()
    spent = store.spend_since(month_start(now, config.tz))
    max_gap = timedelta(minutes=3 * config.snapshot_interval_minutes)
    summary = queries.status_summary(store, now, max_gap)
    return format_status(summary, spent, config, service.status())


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
        try:
            print(agent.respond(CLI_CHAT_ID, line))
        except KeyboardInterrupt:
            print("(stopped)")
        except Exception:
            logging.getLogger(__name__).exception("chat turn failed")
            print(UNEXPECTED_MSG)


def _cmd_ask(agent: Agent, question: str) -> int:
    chat_id = f"ask-{uuid.uuid4().hex}"  # one-shot: no shared history with `chat`
    try:
        print(agent.respond(chat_id, question))
    except Exception:
        logging.getLogger(__name__).exception("ask failed")
        print(UNEXPECTED_MSG, file=sys.stderr)
        return 1
    finally:
        agent.reset(chat_id)
    return 0


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
    svc = sub.add_parser("service", help="record plays in the background (and run the bot)")
    svc.add_argument("action", choices=["install", "uninstall", "status"])
    run = sub.add_parser("run", help="run the Telegram bot in the foreground")
    run.add_argument(
        "--no-scheduler", action="store_true", help="don't take snapshots in this process"
    )
    run.add_argument("--service", action="store_true", help=argparse.SUPPRESS)
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
    trim_job_logs()
    store = Store(db_path())

    if args.command == "snapshot":
        return _cmd_snapshot(store)
    if args.command == "status":
        print(_status_text(config, store))
        return 0
    if args.command == "service":
        return _cmd_service(args.action, config)
    if args.command in ("chat", "ask"):
        key = _require_key()
        if not key:
            return 2
        agent = build_agent(config, store, key)
        if args.command == "ask":
            return _cmd_ask(agent, " ".join(args.question))
        return _cmd_chat(agent)
    if args.command == "run":
        from .bot import run_bot  # imported lazily: only `run` needs python-telegram-bot

        return run_bot(config, store, no_scheduler=args.no_scheduler, service=args.service)
    return 2
