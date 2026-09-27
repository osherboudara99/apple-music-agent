# music-agent

Ask questions about your Apple Music listening and build playlists, from your terminal or (optionally) Telegram. It runs on your Mac and reads your library from Music.app. **macOS only.**

> Status: Phase 1 (developer preview). A setup wizard, background service and PyPI release are coming in Phase 2.

## What it can do

- "How many songs have I listened to today?" / "…this year?"
- "Make a playlist of what I listened to in the last 24 hours."
- "Make a playlist of the rock I played this past week."
- "Make a rainy-day playlist from what I listened to this week."
- "Who are my most played artists of all time?"

Playlists are created in a **Music Agent** folder in your library. The agent never edits or deletes existing playlists.

## Requirements

- macOS with the Music app and your library on this Mac (turn on **Sync Library** to include plays from your iPhone and other devices)
- [uv](https://docs.astral.sh/uv/)
- An [Anthropic API key](https://console.anthropic.com/). The default model (Claude Haiku 4.5) costs about $0.02 per question; the monthly budget defaults to $5.

## Quickstart (from a clone)

```bash
uv sync
uv run keyring set music-agent anthropic_api_key   # paste your key; stored in the macOS Keychain
uv run music-agent snapshot                         # records a baseline; approve the Music permission prompt
uv run music-agent chat
```

Other commands: `ask "<question>"`, `snapshot`, `status`, `run` (Telegram).

## Why snapshots?

Music.app keeps no listening history, only each song's total play count and last-played date. music-agent takes a snapshot every 10 minutes and compares counts, which builds the history Apple doesn't keep. Counting distinct songs works from day one. **Repeat-play counts and past windows ("last Tuesday") only count from the first snapshot.** Keep `music-agent run` going, or run `music-agent snapshot` regularly (Phase 2 adds a background service).

## Telegram (optional)

1. In Telegram, message **@BotFather**, send `/newbot`, and copy the token.
2. `uv run keyring set music-agent telegram_bot_token`
3. `uv run music-agent run`, then message your bot. It replies with your user ID.
4. Add `telegram_allowed_user_id = <your id>` to `~/Library/Application Support/music-agent/config.toml` and restart `run`.

## Configuration

`~/Library/Application Support/music-agent/config.toml`:

```toml
timezone = "America/Los_Angeles"   # detected automatically
model = "claude-haiku-4-5"         # e.g. "claude-sonnet-5" for better themed playlists
monthly_budget_usd = 5.0
telegram_allowed_user_id = 123456789
snapshot_interval_minutes = 10
playlist_folder = "Music Agent"
```

## Limitations

- Songs you stream without adding them to your library aren't visible in Music.app, so they can't be counted.
- Plays from other devices arrive through iCloud Sync Library (about a minute in testing, but Apple doesn't guarantee it). `music-agent status` shows the measured lag.
- The Mac must be on and awake for Telegram answers.
- Themed playlists use Claude's knowledge of the songs; there's no audio analysis.

## Privacy

Song titles, artists, albums and genres in tool results are sent to Anthropic's API to answer your questions. If Telegram is enabled, messages pass through Telegram's servers. Nothing else leaves your Mac.

## Development

```bash
uv run pytest            # unit tests (never touch your library)
uv run pytest -m mac     # integration tests against your real Music library (creates and deletes a test playlist)
uv run ruff check
```
