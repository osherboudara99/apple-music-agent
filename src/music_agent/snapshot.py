"""Turn successive library snapshots into play events."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from . import store as st
from .models import PlayEvent, Track, utcnow

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiffResult:
    events: list[PlayEvent]
    removed_ids: list[str]
    warnings: list[str]
    baseline: list[Track]  # what to store: live data, except play counts never go down


def diff(
    stored: dict[str, Track],
    live: list[Track],
    prev_snapshot_at: datetime | None,
    now: datetime,
    last_seen: dict[str, datetime] | None = None,
) -> DiffResult:
    events: list[PlayEvent] = []
    warnings: list[str] = []
    baseline: list[Track] = []
    live_ids: set[str] = set()
    for track in live:
        live_ids.add(track.persistent_id)
        old = stored.get(track.persistent_id)
        if prev_snapshot_at is None or old is None or track.played_count >= old.played_count:
            baseline.append(track)
        else:
            # High-water mark: a lower count is usually a transient or misaligned read
            # (iCloud reloading). Storing it would turn the restore into phantom plays.
            baseline.append(
                replace(track, played_count=old.played_count, played_date=old.played_date)
            )
        if prev_snapshot_at is None:
            continue
        if old is None:
            if (
                track.played_count > 0
                and track.played_date is not None
                and track.played_date > prev_snapshot_at
            ):
                events.append(PlayEvent(track.persistent_id, track.played_date, None, now, False))
                # A song added and played several times between snapshots: only the latest
                # play's time is known; the others happened after it was added.
                extra_start = min(track.date_added or prev_snapshot_at, track.played_date)
                for _ in range(track.played_count - 1):
                    events.append(
                        PlayEvent(track.persistent_id, track.played_date, extra_start, now, True)
                    )
            continue
        delta = track.played_count - old.played_count
        if delta < 0:
            warnings.append(
                f"play count went down for {track.artist} - {track.name} "
                f"({old.played_count} -> {track.played_count}); "
                f"keeping {old.played_count} until it is exceeded"
            )
            continue
        if delta == 0:
            continue
        exact = track.played_date is not None and (
            old.played_date is None or track.played_date > old.played_date
        )
        # Never back-date: if played_date didn't advance, the plays still happened since
        # the last snapshot, so date them "now" within that window.
        played_at = track.played_date if exact else now
        # The uncertain plays happened after this track was last seen (it may have been
        # missing from partial reads since), and never after the play that bounds them.
        seen = (last_seen or {}).get(track.persistent_id) or prev_snapshot_at
        window_start = min(seen, played_at)
        if exact:
            events.append(PlayEvent(track.persistent_id, played_at, None, now, False))
        else:
            events.append(PlayEvent(track.persistent_id, played_at, window_start, now, True))
        for _ in range(delta - 1):
            events.append(PlayEvent(track.persistent_id, played_at, window_start, now, True))
    removed = sorted(set(stored) - live_ids)
    return DiffResult(events, removed, warnings, baseline)


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
        # Diff against every stored track, removed ones included: a track missing from one
        # (partial) read keeps its counts when it comes back instead of looking brand new.
        stored = st.load_all_tracks(conn)
        if not live:
            # Never a valid baseline or update: usually a transient read while iCloud reloads.
            error = (
                "Music.app returned 0 tracks; skipping this snapshot "
                "(if your library is really empty, add some music first)"
            )
            st.record_snapshot(conn, now, 0, 0, int((time.monotonic() - started) * 1000), error)
            log.error("snapshot failed: %s", error)
            return SnapshotResult(now, 0, 0, False, [], error=error)
        prev = st.last_snapshot_at(conn)
        result = diff(stored, live, prev, now, st.load_last_seen(conn))
        st.upsert_tracks(conn, result.baseline, now)
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
