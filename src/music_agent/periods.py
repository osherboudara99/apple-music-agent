"""Turn "today", "past_week", explicit dates, ... into UTC [start, end) windows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

PERIOD_NAMES = ("today", "last_24h", "this_week", "past_week", "this_month", "this_year")


class PeriodError(ValueError):
    """The requested time window is invalid."""


@dataclass(frozen=True)
class Period:
    start: datetime  # aware, UTC
    end: datetime  # aware, UTC, exclusive
    label: str


def _utc(dt: datetime) -> datetime:
    return dt.astimezone(UTC)


def _parse(value: str, tz: ZoneInfo, is_end: bool) -> datetime:
    text = value.strip()
    try:
        if len(text) == 10:
            day = date.fromisoformat(text)
            if is_end:
                day += timedelta(days=1)
            return _utc(datetime.combine(day, time(0), tzinfo=tz))
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise PeriodError(f"Could not read date {value!r}; use ISO format like 2026-09-01") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return _utc(parsed)


def resolve(
    period: str | None, start: str | None, end: str | None, tz: ZoneInfo, now: datetime
) -> Period:
    if period:
        if start or end:
            raise PeriodError("Pass either period or start/end, not both.")
        name = period.strip().lower()
        now_local = now.astimezone(tz)
        midnight = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        if name == "today":
            begin = midnight
        elif name == "last_24h":
            begin = now - timedelta(hours=24)
        elif name == "this_week":
            begin = midnight - timedelta(days=now_local.weekday())
        elif name == "past_week":
            begin = now - timedelta(days=7)
        elif name == "this_month":
            begin = midnight.replace(day=1)
        elif name == "this_year":
            begin = midnight.replace(month=1, day=1)
        else:
            raise PeriodError(
                f"Unknown period {period!r}. Use one of: {', '.join(PERIOD_NAMES)}, "
                "or start/end dates."
            )
        return Period(_utc(begin), _utc(now), name)
    if not start:
        raise PeriodError("Give a period or a start date.")
    begin = _parse(start, tz, is_end=False)
    finish = _parse(end, tz, is_end=True) if end else _utc(now)
    if finish <= begin:
        raise PeriodError("end must be after start.")
    return Period(begin, finish, f"{start}..{end or 'now'}")
