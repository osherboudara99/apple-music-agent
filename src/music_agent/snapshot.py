"""Turn successive library snapshots into play events."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from . import store as st
from .models import PlayEvent, Track, utcnow

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiffResult:
    events: list[PlayEvent]
    removed_ids: list[str]
    warnings: list[str]


def diff(
    stored: dict[str, Track], live: list[Track], prev_snapshot_at: datetime | None, now: datetime
) -> DiffResult:
    events: list[PlayEvent] = []
    warnings: list[str] = []
    live_ids: set[str] = set()
    for track in live:
        live_ids.add(track.persistent_id)
        if prev_snapshot_at is None:
            continue
        old = stored.get(track.persistent_id)
        if old is None:
            if (
                track.played_count > 0
                and track.played_date is not None
                and track.played_date > prev_snapshot_at
            ):
                events.append(PlayEvent(track.persistent_id, track.played_date, None, now, False))
            continue
        delta = track.played_count - old.played_count
        if delta < 0:
            warnings.append(
                f"play count went down for {track.artist} - {track.name} "
                f"({old.played_count} -> {track.played_count}); resetting baseline"
            )
            continue
        if delta == 0:
            continue
        exact = track.played_date is not None and (
            old.played_date is None or track.played_date > old.played_date
        )
        played_at = track.played_date or now
        if exact:
            events.append(PlayEvent(track.persistent_id, played_at, None, now, False))
        else:
            events.append(PlayEvent(track.persistent_id, played_at, prev_snapshot_at, now, True))
        for _ in range(delta - 1):
            events.append(PlayEvent(track.persistent_id, played_at, prev_snapshot_at, now, True))
    removed = sorted(set(stored) - live_ids)
    return DiffResult(events, removed, warnings)


@dataclass(frozen=True)
class SnapshotResult:
    taken_at: datetime
    track_count: int
    events_added: int
    first_run: bool
    warnings: list[str]
    error: str | None = None


def _record_failure(store: st.Store, at: datetime, started: float, error: str) -> SnapshotResult:
    with store.transaction() as conn:
        st.record_snapshot(conn, at, 0, 0, int((time.monotonic() - started) * 1000), error=error)
    log.error("snapshot failed: %s", error)
    return SnapshotResult(at, 0, 0, False, [], error=error)


def run_snapshot(
    store: st.Store,
    read_library: Callable[[], list[Track]],
    clock: Callable[[], datetime] = utcnow,
) -> SnapshotResult:
    started = time.monotonic()
    try:
        live = read_library()
    except Exception as exc:  # noqa: BLE001 - any read failure is recorded, not raised
        return _record_failure(store, clock(), started, str(exc))

    with store.transaction() as conn:
        now = clock()
        stored = st.load_active_tracks(conn)
        if not live and stored:
            error = "Music.app returned 0 tracks; skipping this snapshot"
            st.record_snapshot(conn, now, 0, 0, int((time.monotonic() - started) * 1000), error)
            log.error("snapshot failed: %s", error)
            return SnapshotResult(now, 0, 0, False, [], error=error)
        prev = st.last_snapshot_at(conn)
        result = diff(stored, live, prev, now)
        st.upsert_tracks(conn, live, now)
        st.mark_removed(conn, result.removed_ids, now)
        st.insert_plays(conn, result.events)
        if prev is None:
            st.set_meta(conn, "install_at", st.to_iso(now))
        duration_ms = int((time.monotonic() - started) * 1000)
        st.record_snapshot(conn, now, len(live), len(result.events), duration_ms)
    for warning in result.warnings:
        log.warning(warning)
    return SnapshotResult(now, len(live), len(result.events), prev is None, result.warnings)


def maybe_snapshot(
    store: st.Store,
    read_library: Callable[[], list[Track]],
    max_age: timedelta = timedelta(seconds=60),
    clock: Callable[[], datetime] = utcnow,
) -> SnapshotResult | None:
    with store.connect() as conn:
        last = st.last_snapshot_at(conn)
    if last is not None and clock() - last < max_age:
        return None
    return run_snapshot(store, read_library, clock)
