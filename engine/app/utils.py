from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(dt: datetime | None) -> datetime | None:
    """Localize naive datetimes (e.g. from MongoDB) to UTC for comparison with utc_now()."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def floor_to_minute(dt: datetime) -> datetime:
    return dt.replace(second=0, microsecond=0)


def is_equity_tradable_now(now: datetime | None = None) -> bool:
    current = now or utc_now()
    et = current.astimezone(ZoneInfo("America/New_York"))
    if et.weekday() >= 5:
        return False
    start = et.replace(hour=9, minute=30, second=0, microsecond=0)
    end = et.replace(hour=16, minute=0, second=0, microsecond=0)
    skip_end = start + timedelta(minutes=15)
    if not (start <= et <= end):
        return False
    if et < skip_end:
        return False
    return True

