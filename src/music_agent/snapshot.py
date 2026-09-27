"""Turn successive library snapshots into play events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .models import PlayEvent, Track


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
