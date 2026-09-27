from datetime import datetime, timezone

from music_agent.models import Track


def dt(s: str) -> datetime:
    """'2026-09-27T18:00:00' (UTC) -> aware UTC datetime."""
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def make_track(pid: str = "A", **overrides) -> Track:
    values = dict(
        persistent_id=pid,
        name=f"Song {pid}",
        artist="Artist",
        album="Album",
        genre="Rock",
        duration_s=200.0,
        date_added=None,
        played_count=0,
        played_date=None,
    )
    values.update(overrides)
    return Track(**values)
