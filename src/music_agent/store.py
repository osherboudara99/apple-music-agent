"""SQLite storage. Each operation opens its own connection, so threads can share a Store."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .models import PlayEvent, Track

SCHEMA_VERSION = "1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    persistent_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    artist TEXT NOT NULL,
    album TEXT NOT NULL,
    genre TEXT NOT NULL,
    duration_s REAL NOT NULL,
    date_added TEXT,
    played_count INTEGER NOT NULL,
    played_date TEXT,
    last_seen_at TEXT NOT NULL,
    removed_at TEXT
);
CREATE INDEX IF NOT EXISTS tracks_played_date ON tracks(played_date);
CREATE TABLE IF NOT EXISTS plays (
    id INTEGER PRIMARY KEY,
    persistent_id TEXT NOT NULL,
    played_at TEXT NOT NULL,
    window_start TEXT,
    detected_at TEXT NOT NULL,
    approx INTEGER NOT NULL,
    source TEXT NOT NULL DEFAULT 'snapshot'
);
CREATE INDEX IF NOT EXISTS plays_played_at ON plays(played_at);
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY,
    taken_at TEXT NOT NULL,
    track_count INTEGER NOT NULL,
    events_added INTEGER NOT NULL,
    duration_ms INTEGER NOT NULL,
    error TEXT
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY,
    chat_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS conversations_chat ON conversations(chat_id, id);
CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cache_read_tokens INTEGER NOT NULL,
    cost_usd REAL NOT NULL
);
"""


def to_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def from_iso(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
                (SCHEMA_VERSION,),
            )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """BEGIN IMMEDIATE: takes the write lock up front, so concurrent writers serialize."""
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")

    def append_messages(self, chat_id: str, messages: list[dict], at: datetime) -> None:
        with self.transaction() as conn:
            conn.executemany(
                "INSERT INTO conversations (chat_id, role, content_json, created_at) "
                "VALUES (?, ?, ?, ?)",
                [
                    (chat_id, m["role"], json.dumps(m, ensure_ascii=False), to_iso(at))
                    for m in messages
                ],
            )

    def load_messages(self, chat_id: str) -> list[tuple[dict, datetime]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT content_json, created_at FROM conversations "
                "WHERE chat_id = ? ORDER BY id",
                (chat_id,),
            ).fetchall()
        return [(json.loads(r["content_json"]), from_iso(r["created_at"])) for r in rows]

    def clear_messages(self, chat_id: str) -> None:
        with self.transaction() as conn:
            conn.execute("DELETE FROM conversations WHERE chat_id = ?", (chat_id,))

    def record_usage(
        self,
        at: datetime,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        cost_usd: float,
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO usage (at, model, input_tokens, output_tokens, cache_read_tokens, "
                "cost_usd) VALUES (?, ?, ?, ?, ?, ?)",
                (to_iso(at), model, input_tokens, output_tokens, cache_read_tokens, cost_usd),
            )

    def spend_since(self, since: datetime) -> float:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM usage WHERE at >= ?", (to_iso(since),)
            ).fetchone()
        return float(row[0])


def _row_to_track(row: sqlite3.Row) -> Track:
    return Track(
        persistent_id=row["persistent_id"],
        name=row["name"],
        artist=row["artist"],
        album=row["album"],
        genre=row["genre"],
        duration_s=row["duration_s"],
        date_added=from_iso(row["date_added"]),
        played_count=row["played_count"],
        played_date=from_iso(row["played_date"]),
    )


def load_active_tracks(conn: sqlite3.Connection) -> dict[str, Track]:
    rows = conn.execute("SELECT * FROM tracks WHERE removed_at IS NULL")
    return {row["persistent_id"]: _row_to_track(row) for row in rows}


def load_last_seen(conn: sqlite3.Connection) -> dict[str, datetime]:
    """When each track was last present in a successful library read."""
    rows = conn.execute("SELECT persistent_id, last_seen_at FROM tracks")
    return {r["persistent_id"]: from_iso(r["last_seen_at"]) for r in rows}


def load_all_tracks(conn: sqlite3.Connection) -> dict[str, Track]:
    """Every track ever seen, including removed ones (so a returning track keeps its counts)."""
    rows = conn.execute("SELECT * FROM tracks")
    return {row["persistent_id"]: _row_to_track(row) for row in rows}


def upsert_tracks(conn: sqlite3.Connection, tracks: list[Track], seen_at: datetime) -> None:
    conn.executemany(
        """
        INSERT INTO tracks (persistent_id, name, artist, album, genre, duration_s, date_added,
                            played_count, played_date, last_seen_at, removed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
        ON CONFLICT(persistent_id) DO UPDATE SET
            name = excluded.name, artist = excluded.artist, album = excluded.album,
            genre = excluded.genre, duration_s = excluded.duration_s,
            date_added = excluded.date_added, played_count = excluded.played_count,
            played_date = excluded.played_date, last_seen_at = excluded.last_seen_at,
            removed_at = NULL
        """,
        [
            (
                t.persistent_id, t.name, t.artist, t.album, t.genre, t.duration_s,
                to_iso(t.date_added), t.played_count, to_iso(t.played_date), to_iso(seen_at),
            )
            for t in tracks
        ],
    )


def mark_removed(conn: sqlite3.Connection, ids: list[str], at: datetime) -> None:
    conn.executemany(
        "UPDATE tracks SET removed_at = ? WHERE persistent_id = ? AND removed_at IS NULL",
        [(to_iso(at), pid) for pid in ids],
    )


def insert_plays(conn: sqlite3.Connection, events: list[PlayEvent]) -> None:
    conn.executemany(
        """
        INSERT INTO plays (persistent_id, played_at, window_start, detected_at, approx, source)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (
                e.persistent_id, to_iso(e.played_at), to_iso(e.window_start),
                to_iso(e.detected_at), int(e.approx), e.source,
            )
            for e in events
        ],
    )


def record_snapshot(
    conn: sqlite3.Connection,
    taken_at: datetime,
    track_count: int,
    events_added: int,
    duration_ms: int,
    error: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO snapshots (taken_at, track_count, events_added, duration_ms, error)
        VALUES (?, ?, ?, ?, ?)
        """,
        (to_iso(taken_at), track_count, events_added, duration_ms, error),
    )


def last_snapshot_at(conn: sqlite3.Connection) -> datetime | None:
    row = conn.execute(
        "SELECT taken_at FROM snapshots WHERE error IS NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return from_iso(row["taken_at"]) if row else None


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
