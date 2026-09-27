# music-agent — Design

**Date:** 2026-09-27
**Status:** Approved in brainstorming; awaiting spec review

## Goal

An installable, developer-first macOS tool that answers questions about the user's Apple Music listening and creates playlists from it. Each user installs it on their own Mac and adds their own Anthropic API key. They talk to it from the **command line** (`music-agent chat` / `music-agent ask`), and can **optionally** add their own Telegram bot to reach it from their phone. The Telegram bot can run in the foreground, at login, or always-on on a dedicated Mac (e.g. a Mac mini).

Example requests:

- "How many songs have I listened to today?" / "…this year?"
- "Create a playlist of the songs I listened to in the last 24 hours."
- "Create a playlist of the rock songs I listened to in the past week."
- "Create a playlist with <theme> based on what I listened to in the past week."
- General stats questions (top artists, genres, most played, etc.).

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Where it runs | Locally on the user's Mac | Play counts and last-played dates are only exposed on-device (Music.app). The Apple Music web API has no per-song play counts, and its recently-played list is capped at 50 tracks with no timestamps. Cloud options (API-only, hybrid, rented Mac) were rejected as lower-accuracy or costly. |
| Agent harness | Own Python bot + a hand-written tool-use loop over the Claude Messages API | Testable end to end, runs as a plain launchd process, the model can only call the tools we pass, no research-preview dependency, no MCP needed. The loop is hand-written rather than the SDK's beta tool runner so history and per-call usage can be persisted and the client faked in tests. |
| Model | Default `claude-haiku-4-5`, configurable | Cheap (~$0.02/question). Users can switch to `claude-sonnet-5` if themed-playlist quality is poor. |
| Chat interface | Command line always (`chat`, `ask`); Telegram optional | The CLI needs no extra setup. Telegram adds phone access: a bot per user, long polling, so it works behind NAT with no public endpoint. Each user needs their own bot because it must run on the Mac holding their library. |
| History | Start from install; no backfill now | Schema keeps a `source` column so a privacy.apple.com import can be added later. |
| Audience / distribution | Developer-first: PyPI package `music-agent`, installed with `uv tool install` or `pipx` | Simplest to build and maintain. A Homebrew tap can be added later without code changes. |
| Name | Package and command `music-agent` | Short, generic, and keeps Apple's trademark out of the product name. The README says "for Apple Music on macOS". |
| Secrets | macOS Keychain via `keyring` | No plaintext API keys on disk. |
| Config | TOML in `~/Library/Application Support/music-agent/config.toml` | Human-editable; written by the setup wizard. |
| Platform | macOS only; Python ≥ 3.11 | Music.app scripting exists only on macOS. The CLI exits with a clear message elsewhere. |
| License | MIT (existing) | — |

## Verified facts (probed on the author's Mac, 2026-09-27)

- JXA (`osascript -l JavaScript`) reads all 31,699 library tracks' properties in bulk in ~0.6s.
- `tracks.whose({playedDate: {_greaterThan: d}})` filters in Music.app. A `whose()` that matches nothing throws on property read with `errorNumber === -1728`, which must be treated as empty.
- Creating a playlist, duplicating tracks into it, and deleting it via JXA works (~1s).
- An iPhone play appeared on the Mac within ~1 minute, and `playedDate` carries the phone's own play time. (One sample; CarPlay and Windows untested.)
- Each track only has `playedCount` (cumulative) and `playedDate` (last play). There is no per-play event log.
- Genres are granular (Rock, Hard Rock, Alternative, Metal, …).
- Both `music-agent` and `apple-music-agent` were unclaimed on PyPI.

## Known limitations (documented in the README)

1. **Total plays (with repeats) only count from install day.** Distinct-tracks-played for any period (including "this year") is exact from day one, because it comes from each track's last-played date.
2. **Songs not in the library are invisible.** Catalog/radio streams that were never added to the library don't appear in Music.app.
3. **Repeat-play timing can be approximate.** If a track's count rises by N between snapshots, only the newest play has an exact time; the others fall between the two snapshots.
4. **The Mac must be on, awake and logged in** for the bot to answer. Missed snapshots lose no plays (counts are cumulative), only timing precision.
5. **Themed playlists are judgement-based.** The library has no mood or tempo data; Claude picks by its knowledge of titles and artists.
6. **Sync lag depends on Apple.** Plays from other devices reach the Mac via iCloud Sync Library; one test showed ~1 minute, but Apple doesn't guarantee it. `status` reports measured lag.

## Architecture

```
iPhone / CarPlay / Windows --iCloud sync--> Music.app (Mac)
                                               ^    |
                                  JXA read /   |    | every 10 min
                                  create       |    v
Terminal <--> cli.py ---+
                         +--> agent.py (tool runner) --> tools.py --> queries.py / music.py
Telegram <--> bot.py ---+     (Claude API)                             |
 (optional)  (allowlist)                                               |
                                                                   v
                                                            SQLite plays.db
```

### Components (`src/music_agent/`)

Each unit has one job. `music.py` is the only module that talks to Music.app, and `config.py` is the only one that touches the Keychain or config file.

| Module | Responsibility | Depends on |
|---|---|---|
| `music.py` + `jxa/*.js` | Run JXA scripts: bulk-read library tracks; create a playlist (in a "Music Agent" folder) from persistent IDs. Returns plain dataclasses. | `osascript` |
| `config.py` | Load/save `config.toml`; get/set secrets in the Keychain (service name `music-agent`); expose a typed `Config`. | `tomllib`, `tomli-w`, `keyring` |
| `store.py` | SQLite schema, migrations, read/write helpers. WAL mode with a busy timeout, since the snapshot job and the bot write concurrently. | `sqlite3` |
| `snapshot.py` | Pure diff function (old rows, new rows, times → events + updated rows) plus `run_snapshot()` wiring `music` → diff → `store`. | `music`, `store` |
| `periods.py` | Resolve `today`, `last_24h`, `this_week`, `past_week`, `this_month`, `this_year`, or explicit ISO dates to `[start, end)` in the configured time zone. | `zoneinfo` |
| `genres.py` | Genre → family mapping (e.g. rock = Rock, Hard Rock, Alternative, Metal, Punk, Grunge…); unknown genres map to themselves. | — |
| `queries.py` | Stats and track lists over `store` and live library data. | `store`, `periods`, `genres` |
| `tools.py` | The six Claude tools (`@beta_tool` functions), thin wrappers over `queries`/`music`. Each response includes the current local time. | `queries`, `music`, `snapshot` |
| `agent.py` | System prompt, tool runner call, per-chat history, idle reset, token/cost accounting, budget check. | `anthropic`, `tools`, `store`, `config` |
| `bot.py` | Optional Telegram front end: long polling, user-ID allowlist, `/new` and `/status`, forwards text to `agent`. | `python-telegram-bot`, `agent` |
| `setup_wizard.py` | The interactive `setup` flow (below). Each step is idempotent. | `config`, `music`, `snapshot`, `service` |
| `doctor.py` | Health checks, each returning pass/fail plus an exact fix. | `config`, `music`, `store`, `service` |
| `service.py` | Generate, install, uninstall and query launchd agents. | `launchctl` |
| `cli.py` | Entry point `music-agent` (declared in `pyproject.toml` `[project.scripts]`). | all |

## Data model (SQLite)

Data folder: `~/Library/Application Support/music-agent/` (`config.toml`, `plays.db`, `logs/`).

- `tracks(persistent_id PK, name, artist, album, genre, duration_s, date_added, played_count, played_date, last_seen_at, removed_at)`
- `plays(id PK, persistent_id, played_at, window_start, detected_at, approx BOOL, source TEXT DEFAULT 'snapshot')`
- `snapshots(id PK, taken_at, track_count, events_added, duration_ms, error)`
- `meta(key PK, value)`: `install_at`, `schema_version`
- `conversations(chat_id, role, content_json, created_at)`: agent history
- `usage(id PK, at, input_tokens, output_tokens, cache_read_tokens, cost_usd)`: spend tracking

## Snapshot algorithm

**Why:** Music.app keeps no listening history, only each track's lifetime play count and last-played date, and each new play overwrites that date. Without snapshots the agent can answer distinct-songs, recent-playlist and all-time questions, but not repeat-play counts, past windows ("last Tuesday", "in October") or trends. History can't be reconstructed later, so snapshots start at install. The README explains this in the same terms.

Runs every 10 minutes (launchd job, or the built-in scheduler in `run`), and on demand (throttled to at most once per 60s) before any tool call that touches the last 24 hours.

1. Bulk-read all library tracks, plus a second read of the track ids. Reject the read (recorded as a failed snapshot, retried next time) if the property columns don't line up or the ids changed mid-read.
   A read that returns 0 tracks is always a failed snapshot (never a baseline).
2. For each track, compare with its stored row:

| Situation | Action |
|---|---|
| First ever run | Store baseline; record no events; set `meta.install_at`. |
| `played_count` rose by N > 0 | Insert N events. If `played_date` advanced: newest `played_at = played_date`, `approx = 0`. Otherwise, and for the other N−1: `played_at = played_date` (or *now* if it didn't advance; never back-dated), `window_start = previous snapshot time`, `approx = 1`. |
| New track (not stored) with `played_count > 0` and `played_date > previous snapshot time` | Insert 1 exact event at `played_date`, plus `played_count − 1` approximate events whose window starts at `date_added` (never after `played_date`). |
| New track otherwise | Baseline only. |
| `played_count` decreased | **High-water mark:** keep the stored count and date (metadata still updates); no events; log a warning. A lower count is usually a transient or misaligned read, and storing it would turn the restore into phantom plays. Cost: after a genuine decrease, plays aren't counted until the count passes the old value. |
| Track missing from library | Set `removed_at` (first time only); keep the row. Snapshots diff against every stored row, removed ones included, so a track missing from one partial read keeps its counts when it returns: its plays are counted and no phantom plays appear. |

3. Every event stores `detected_at`, which gives ongoing sync-lag measurements (`detected_at − played_at`).
4. **Concurrency:** the launchd snapshot job and the bot's on-demand snapshot can overlap. Steps 2–3 run inside one `BEGIN IMMEDIATE` transaction that reads the stored rows *inside* the transaction, so two overlapping snapshots can't both count the same play increase. The library read (step 1) happens before the transaction; the second writer simply sees no remaining difference.

## Answer sources

| Question | Source | Accuracy |
|---|---|---|
| Distinct songs in a period | Live library: `played_date ≥ period start` | Exact from day one |
| Total plays in a period (repeats) | `plays` table | Exact from `install_at`; the agent states the start date when the period begins before it |
| All-time top tracks/artists/genres | Library `played_count` | Exact (lifetime counts) |
| Playlists from recent plays | `plays` ∪ live library last-played, filtered by genre family | Exact membership |

## Tools exposed to Claude

| Tool | Parameters | Returns |
|---|---|---|
| `listening_stats` | `period` or `start`/`end`; optional `genre_family`, `genres` | plays, distinct tracks, distinct artists, top 5 tracks/artists/genres, `plays_counted_since`, `has_approx_times`, `distinct_is_lower_bound` (closed past window starting before install: distinct counts/rankings may miss songs replayed later), `plays_near_boundary` (approximate repeat plays whose uncertainty window starts before the period), `now` |
| `played_tracks` | same filters + `limit` (default 200) | list of `{id, name, artist, album, genre, plays_in_range, last_played}` |
| `all_time_top` | `by` ∈ {track, artist, genre}, `limit` | ranked list with lifetime play counts |
| `list_genres` | optional `period` | genres with track/play counts and their family |
| `search_library` | optional `query`, `artist`, `genre`, `limit` | matching tracks |
| `create_playlist` | `name`, `track_ids`, optional `description` | `{name, track_count, missing_track_ids, sample_tracks}`; adds a date suffix if the name exists. Refused before touching Music.app if none of the ids are known. The final name (with date suffix if taken) and description are set in the creating call: renaming right after creation reverts (Music.app keeps and iCloud syncs the creation name; verified 2026-09-27). The description is Claude's one-sentence summary plus "Generated by music-agent on <date>." The script prints the new playlist's persistent id to stderr immediately; on failure or zero matching tracks it deletes the playlist itself, and after a timeout Python deletes exactly that id (only inside the agent folder). Playlists can't be made public by script (`shared` is not writable, error -54), so visibility is the Music app's default. |

No tool edits or deletes existing playlists or tracks. Library-wide tools (`all_time_top`, `search_library`, `list_genres` without a window) refresh the snapshot first (throttled to once per 60s). `ask` is one-shot (fresh history each call). The Claude client is created with an explicit key and `base_url`, so shell `ANTHROPIC_*` settings can't redirect or replace it.

## Agent behavior (system prompt rules)

- Uses the configured time zone. "Today" = since local midnight; "past week" / "last 7 days" = rolling 7×24h; "this week" = since Monday 00:00; "this year" = since Jan 1.
- Short plain-text replies suited to a phone.
- Mention caveats only when relevant (e.g. "total plays counted since <install date>", approximate times).
- Create playlists immediately when asked; reply with name, count and first few tracks.
- Themed playlists: if fewer than ~5 tracks fit, say so and offer to widen the window (past month or whole library) instead of padding.
- On tool errors, say what failed; never invent numbers.
- If `plays_near_boundary` > 0, say about that many counted plays may be from just before the period.
- The monthly budget is checked before every API call, not just once per question.
- Conversation history persists per chat; it resets after 30 minutes idle or on `/new`.

## Configuration

`config.toml` (non-secret):

```toml
timezone = "America/Los_Angeles"   # detected from the system during setup
model = "claude-haiku-4-5"
monthly_budget_usd = 5.0
telegram_allowed_user_id = 123456789   # only present if Telegram is set up
snapshot_interval_minutes = 10
playlist_folder = "Music Agent"
```

Keychain (service `music-agent`): `anthropic_api_key`, and `telegram_bot_token` if Telegram is set up. Environment variables `MUSIC_AGENT_ANTHROPIC_API_KEY` / `MUSIC_AGENT_TELEGRAM_BOT_TOKEN` override the Keychain (useful for development and CI). The generic `ANTHROPIC_API_KEY` / `TELEGRAM_BOT_TOKEN` are deliberately ignored, so a developer's key for other work is never used or billed by music-agent.

## CLI

| Command | Purpose |
|---|---|
| `music-agent setup` | Interactive wizard (below); safe to re-run |
| `music-agent chat` | Interactive terminal conversation with the agent (multi-turn; `/new` resets, `/exit` or Ctrl-D quits). Same agent, history rules and budget as Telegram. |
| `music-agent ask "<question>"` | One question, one answer; scriptable |
| `music-agent run [--no-scheduler]` | Telegram bot in the foreground, with the snapshot scheduler built in unless disabled. Refuses to start, pointing to `setup`, if Telegram isn't configured. |
| `music-agent snapshot` | Take a snapshot now |
| `music-agent status` | Last snapshot, sync-lag median/p95, month-to-date spend vs budget, service state |
| `music-agent doctor` | Runs every health check; prints pass/fail and the exact fix for each failure |
| `music-agent service install\|uninstall\|status` | Manage the launchd agents |

### Setup wizard

Each step checks whether it's already done and skips if so.

1. **Environment:** confirm macOS, Music.app, and a non-empty library. Make one JXA call so the Automation (Music) permission prompt appears while the user is present.
2. **Anthropic API key:** hidden input, validated with a free API call (`models.list`), saved to the Keychain.
3. **Telegram (optional):** ask "Set up Telegram to reach the agent from your phone? [y/N]". If no, skip; the user can re-run `setup` later. If yes, print the BotFather steps. Validate the pasted token with `getMe` and save it to the Keychain. Ask the user to message the bot, wait on `getUpdates`, confirm "Paired with @username?", and save the user ID to config.
4. **Preferences:** model, monthly budget, time zone (system default, user confirms).
5. **First snapshot:** baseline, then print a summary (track count, distinct tracks played this year).
6. **Background recording and run mode:** recommend `service install` so plays are recorded every 10 minutes even when no command is running (without it, plays are only recorded when the user runs a command, so repeat-play counts are less complete). CLI-only users then get the `chat`/`ask` quickstart. Telegram users also choose: start the bot now in the foreground, install it at login (part of `service install`), or read the always-on guide.

### launchd service

`service install` writes to `~/Library/LaunchAgents/` and loads them with `launchctl bootstrap gui/$UID`. The snapshot job is always installed; the bot job only when Telegram is configured (re-running `service install` after enabling Telegram adds it):

- `io.music-agent.snapshot`: `StartInterval` = interval × 60, runs `<abs path>/music-agent snapshot`.
- `io.music-agent.bot`: `RunAtLoad` + `KeepAlive`, runs `<abs path>/music-agent run --no-scheduler`, wrapped in `caffeinate -s` when the Mac has a battery.
- Logs go to `~/Library/Application Support/music-agent/logs/`.
- The executable path is resolved at install time. `uv tool upgrade` keeps the same path, so upgrades need no reinstall.

## Error handling

| Failure | Behavior |
|---|---|
| Not macOS | The CLI exits with a clear message. |
| Music.app not running | JXA launches it. |
| Automation permission denied (`-1743`) | The tool returns an error; `doctor` prints the System Settings path to grant it. |
| JXA error | The tool returns `{error: …}`; Claude reports it plainly. |
| Claude API error (after the SDK's retries) | Reply "Claude API unavailable, try again shortly"; log the request ID. |
| Missing or invalid Anthropic key | `chat`, `ask` and `run` refuse to start and point to `setup` / `doctor`. |
| Telegram not configured | `run` explains Telegram is optional and how to enable it; everything else works. `doctor` reports Telegram checks as "skipped (not configured)", not failures. |
| Missed snapshots | The next run catches up from cumulative counts. |
| Budget exceeded | The bot refuses new questions until next month; `/status` still works. |
| Message from a non-allowlisted user | Ignored silently; logged. |

## Security and privacy

- If Telegram is enabled, only the configured Telegram user ID is accepted.
- Claude can call only the six tools; none are destructive.
- Secrets live in the Keychain, never in the repo or config file.
- Tool results (song titles, etc.) are untrusted text; the worst outcome is an odd playlist.
- `docs/privacy.md` states what leaves the machine: song, artist, album and genre text in tool results goes to Anthropic's API, and, if Telegram is enabled, chat messages go through Telegram's servers. Nothing else is sent anywhere.

## Documentation

| File | Contents |
|---|---|
| `README.md` | What it does, requirements, quickstart (install, `setup`, `chat`), optional Telegram section, example questions, limitations, costs |
| `docs/always-on-mac-mini.md` | For Telegram users who want the bot reachable 24/7. Dedicated Mac setup: automatic login versus FileVault trade-off, `pmset` (never sleep, `autorestart` after power failure), Sync Library on, Music.app open, SSH and Screen Sharing, `service install`, security implications of an always-signed-in Apple ID |
| `docs/privacy.md` | Data flows, as above |
| `docs/troubleshooting.md` | Organized by `doctor` check |

## Release

- `pyproject.toml`: `name = "music-agent"`, `[project.scripts] music-agent = "music_agent.cli:main"`, dependencies `anthropic`, `python-telegram-bot`, `keyring`, `tomli-w`.
- GitHub Actions CI on a macOS runner: lint and unit tests on every push. Music.app integration tests are excluded because the runner has no library.
- Release on a version tag (`v*`): `uv build`, then publish to PyPI via trusted publishing (no stored token). Versioning is semantic, starting at `0.1.0`.
- Before the first PyPI release: `uv tool install git+https://github.com/osherboudara99/apple-music-agent`.

## Testing

- **Unit (pytest):**
  - the snapshot diff, one test per row of the algorithm table;
  - two overlapping snapshots over the same increase record exactly one set of events;
  - `periods` (DST transitions, Monday boundary, New Year);
  - `genres`;
  - `queries` against a fixture SQLite database;
  - `config` with a fake keyring backend.
- **Agent:** a fake Anthropic client returns scripted `tool_use` turns; asserts that tool dispatch, history persistence, idle reset, usage accounting and the budget cap work. No API cost.
- **Bot:** fake Telegram updates; asserts allowlist rejection, `/new`, `/status`, and the budget message.
- **Setup wizard:** scripted inputs with mocked Telegram and Anthropic calls; the CLI-only path (Telegram declined) completes; re-running skips completed steps.
- **CLI chat:** scripted stdin with the fake Anthropic client; `/new` and `/exit` work; history is kept across turns.
- **Service:** generated plists compared against known-good files; `launchctl` calls mocked.
- **Doctor:** every check in its passing and failing state.
- **Mac integration (`-m mac`, opt-in):**
  - read-only library read and `whose()` empty-match handling;
  - create, verify and delete a probe playlist inside the playlist folder.
- **Live acceptance (manual, ~$0.10):** the five example questions from the Goal section via `music-agent ask`.
- **Final check:** the `verify` skill against this spec.

## Build phases

1. **Core, CLI and bot:** `music`, `config`, `store`, `snapshot`, `periods`, `genres`, `queries`, `tools`, `agent`, then the `chat` / `ask` / `snapshot` / `status` commands (usable end to end from the terminal), then `bot` and `run`. Config and Keychain are used from day one; before the wizard exists, the developer sets secrets through a documented one-line `keyring set` command.
2. **Distribution:** `setup`, `doctor`, `service`, the docs, CI, and the release pipeline.

## Out of scope (for now)

- Cloud deployment, the Apple Music web API, and backfill from the privacy.apple.com export.
- Homebrew tap and a signed/notarized `.app`.
- Windows and Linux.
- Editing or deleting existing playlists.
- Audio analysis or mood features.
- Multiple users per instance.

## Open items to verify during implementation

1. JXA can create a folder playlist and create playlists inside it.
2. launchd-run `osascript` gets (or can be granted) the Automation permission for Music. `doctor` must detect and explain failure either way.
3. Sync lag for CarPlay and Windows plays (measured passively via `detected_at`).
4. PyPI trusted publishing requires a one-time pending-publisher setup on pypi.org by the repo owner.
