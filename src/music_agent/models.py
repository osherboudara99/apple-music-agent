"""Plain data types shared across modules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class Track:
    persistent_id: str
    name: str
    artist: str
    album: str
    genre: str
    duration_s: float
    date_added: datetime | None
    played_count: int
    played_date: datetime | None


@dataclass(frozen=True)
class PlayEvent:
    persistent_id: str
    played_at: datetime
    window_start: datetime | None
    detected_at: datetime
    approx: bool
    source: str = "snapshot"


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)
