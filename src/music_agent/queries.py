"""Read-only questions over the store: stats, track lists, genres, search, status."""

from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import genres as genre_mod
from . import store as st
from .periods import Period


@dataclass(frozen=True)
class TrackRow:
    id: str
    name: str
    artist: str
    album: str
    genre: str
    plays_in_range: int
    last_played: datetime | None
    approx: bool

    def as_dict(self, tz: ZoneInfo) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "artist": self.artist,
            "album": self.album,
            "genre": self.genre,
            "plays_in_range": self.plays_in_range,
            "last_played": local_iso(self.last_played, tz),
        }


def local_iso(dt: datetime | None, tz: ZoneInfo) -> str | None:
    return None if dt is None else dt.astimezone(tz).isoformat(timespec="minutes")


def install_time(store: st.Store) -> datetime | None:
    with store.connect() as conn:
        return st.from_iso(st.get_meta(conn, "install_at"))


_PLAYED_SQL = """
WITH p AS (
    SELECT persistent_id, COUNT(*) AS n, MAX(approx) AS any_approx, MAX(played_at) AS last_in
    FROM plays WHERE played_at >= :s AND played_at < :e
    GROUP BY persistent_id
),
ids AS (
    SELECT persistent_id FROM p
    UNION
    SELECT persistent_id FROM tracks WHERE played_date >= :s AND played_date < :e
)
SELECT t.persistent_id, t.name, t.artist, t.album, t.genre, t.played_date, p.last_in,
       COALESCE(p.n, 0) AS n, COALESCE(p.any_approx, 0) AS any_approx
FROM ids
JOIN tracks t ON t.persistent_id = ids.persistent_id
LEFT JOIN p ON p.persistent_id = ids.persistent_id
"""


def played_tracks(
    store: st.Store,
    period: Period,
    family: str | None = None,
    genres: list[str] | None = None,
    limit: int | None = None,
) -> list[TrackRow]:
    params = {"s": st.to_iso(period.start), "e": st.to_iso(period.end)}
    with store.connect() as conn:
        rows = conn.execute(_PLAYED_SQL, params).fetchall()
    out: list[TrackRow] = []
    for r in rows:
        if not genre_mod.matches(r["genre"], family, genres):
            continue
        played_date = st.from_iso(r["played_date"])
        in_range = played_date is not None and period.start <= played_date < period.end
        # Last play *inside* the window: a later replay must not leak into a past window.
        candidates = [st.from_iso(r["last_in"]), played_date if in_range else None]
        last_in_window = max((c for c in candidates if c is not None), default=None)
        out.append(
            TrackRow(
                id=r["persistent_id"],
                name=r["name"],
                artist=r["artist"],
                album=r["album"],
                genre=r["genre"],
                plays_in_range=max(r["n"], 1 if in_range else 0),
                last_played=last_in_window,
                approx=bool(r["any_approx"]),
            )
        )
    out.sort(
        key=lambda t: (
            -t.plays_in_range,
            -(t.last_played.timestamp() if t.last_played else 0),
            t.artist,
            t.name,
        )
    )
    return out if limit is None else out[:limit]


def completeness(store: st.Store, period: Period, tz: ZoneInfo) -> dict:
    """How complete answers for this window can be, given when recording started."""
    installed = install_time(store)
    with store.connect() as conn:
        last_snapshot = st.last_snapshot_at(conn)
    before_install = installed is None or period.start < installed
    return {
        "plays_is_lower_bound": before_install,
        "plays_counted_since": local_iso(installed, tz),
        # A closed past window from before install can miss songs whose earlier play there was
        # overwritten by a later replay (only the latest played date is known pre-install).
        "distinct_is_lower_bound": before_install
        and (last_snapshot is None or period.end < last_snapshot),
    }


def listening_stats(
    store: st.Store,
    period: Period,
    tz: ZoneInfo,
    family: str | None = None,
    genres: list[str] | None = None,
) -> dict:
    rows = played_tracks(store, period, family, genres)
    artist_plays: Counter[str] = Counter()
    artist_tracks: Counter[str] = Counter()
    genre_plays: Counter[str] = Counter()
    genre_tracks: Counter[str] = Counter()
    for r in rows:
        artist_plays[r.artist] += r.plays_in_range
        artist_tracks[r.artist] += 1
        genre_plays[r.genre] += r.plays_in_range
        genre_tracks[r.genre] += 1
    return {
        "period": {
            "label": period.label,
            "start": local_iso(period.start, tz),
            "end": local_iso(period.end, tz),
        },
        "plays": sum(r.plays_in_range for r in rows),
        **completeness(store, period, tz),
        "distinct_tracks": len(rows),
        "distinct_artists": len(artist_tracks),
        "top_tracks": [
            {"name": r.name, "artist": r.artist, "plays": r.plays_in_range} for r in rows[:5]
        ],
        "top_artists": [
            {"artist": a, "plays": n, "tracks": artist_tracks[a]}
            for a, n in artist_plays.most_common(5)
        ],
        "top_genres": [
            {"genre": g, "plays": n, "tracks": genre_tracks[g]}
            for g, n in genre_plays.most_common(5)
        ],
        "has_approx_times": any(r.approx for r in rows),
        "plays_near_boundary": _plays_near_boundary(store, period, {r.id for r in rows}),
    }


def _plays_near_boundary(store: st.Store, period: Period, ids: set[str]) -> int:
    """Approximate repeat plays counted in the period whose uncertainty window starts before it:
    each is known only to fall between window_start and played_at, so it may belong earlier."""
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT persistent_id FROM plays WHERE approx = 1 AND played_at >= :s "
            "AND played_at < :e AND window_start < :s",
            {"s": st.to_iso(period.start), "e": st.to_iso(period.end)},
        ).fetchall()
    return sum(1 for r in rows if r["persistent_id"] in ids)


def all_time_top(store: st.Store, by: str, limit: int = 10) -> list[dict]:
    with store.connect() as conn:
        if by == "track":
            rows = conn.execute(
                "SELECT name, artist, genre, played_count AS plays FROM tracks "
                "WHERE removed_at IS NULL AND played_count > 0 "
                "ORDER BY played_count DESC, artist, name LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        if by in ("artist", "genre"):
            rows = conn.execute(
                f"SELECT {by}, SUM(played_count) AS plays, COUNT(*) AS tracks FROM tracks "
                f"WHERE removed_at IS NULL AND played_count > 0 "
                f"GROUP BY {by} ORDER BY plays DESC, {by} LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
    raise ValueError(f"by must be 'track', 'artist' or 'genre', not {by!r}")


def list_genres(store: st.Store, period: Period | None = None) -> list[dict]:
    if period is None:
        with store.connect() as conn:
            rows = conn.execute(
                "SELECT genre, COUNT(*) AS tracks, SUM(played_count) AS plays FROM tracks "
                "WHERE removed_at IS NULL GROUP BY genre ORDER BY plays DESC, genre"
            ).fetchall()
        return [
            {
                "genre": r["genre"],
                "family": genre_mod.family_of(r["genre"]),
                "tracks": r["tracks"],
                "plays": r["plays"],
            }
            for r in rows
        ]
    plays: Counter[str] = Counter()
    tracks: Counter[str] = Counter()
    for r in played_tracks(store, period):
        plays[r.genre] += r.plays_in_range
        tracks[r.genre] += 1
    return [
        {"genre": g, "family": genre_mod.family_of(g), "tracks": tracks[g], "plays": n}
        for g, n in plays.most_common()
    ]


def _like(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def search_library(
    store: st.Store,
    query: str | None = None,
    artist: str | None = None,
    genre: str | None = None,
    limit: int = 50,
) -> list[dict]:
    sql = "SELECT * FROM tracks WHERE removed_at IS NULL"
    params: list[str] = []
    if query:
        sql += (
            " AND (name LIKE ? ESCAPE '\\' OR artist LIKE ? ESCAPE '\\'"
            " OR album LIKE ? ESCAPE '\\')"
        )
        params += [_like(query)] * 3
    if artist:
        sql += " AND artist LIKE ? ESCAPE '\\'"
        params.append(_like(artist))
    sql += " ORDER BY played_count DESC, artist, name"
    with store.connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    out = []
    for r in rows:
        if genre and not genre_mod.matches(r["genre"], family=genre):
            continue
        out.append(
            {
                "id": r["persistent_id"],
                "name": r["name"],
                "artist": r["artist"],
                "album": r["album"],
                "genre": r["genre"],
                "lifetime_plays": r["played_count"],
            }
        )
        if len(out) >= limit:
            break
    return out


def track_names(store: st.Store, ids: list[str]) -> dict[str, str]:
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT persistent_id, artist, name FROM tracks "
            f"WHERE persistent_id IN ({placeholders})",
            ids,
        ).fetchall()
    return {r["persistent_id"]: f"{r['artist']} - {r['name']}" for r in rows}


def status_summary(store: st.Store, now: datetime) -> dict:
    with store.connect() as conn:
        last = conn.execute("SELECT * FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
        track_count = conn.execute(
            "SELECT COUNT(*) FROM tracks WHERE removed_at IS NULL"
        ).fetchone()[0]
        plays = conn.execute("SELECT COUNT(*) FROM plays").fetchone()[0]
        lag_rows = conn.execute(
            "SELECT played_at, detected_at FROM plays WHERE approx = 0 AND detected_at >= ?",
            (st.to_iso(now - timedelta(days=30)),),
        ).fetchall()
        installed = st.from_iso(st.get_meta(conn, "install_at"))
    lags = sorted(
        max(0.0, (st.from_iso(r["detected_at"]) - st.from_iso(r["played_at"])).total_seconds() / 60)
        for r in lag_rows
    )
    sync_lag = None
    if lags:
        sync_lag = {
            "median_min": round(statistics.median(lags), 1),
            "p95_min": round(lags[min(len(lags) - 1, int(0.95 * len(lags)))], 1),
            "samples": len(lags),
        }
    return {
        "install_at": installed,
        "track_count": track_count,
        "plays_recorded": plays,
        "last_snapshot": None
        if last is None
        else {
            "at": st.from_iso(last["taken_at"]),
            "track_count": last["track_count"],
            "events_added": last["events_added"],
            "error": last["error"],
        },
        "sync_lag": sync_lag,
    }
