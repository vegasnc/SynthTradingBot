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


def _et_now(now: datetime | None = None) -> datetime:
    """Current time in America/New_York."""
    current = now or utc_now()
    try:
        return current.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        return current.astimezone(ZoneInfo("US/Eastern"))


def is_equity_tradable_now(now: datetime | None = None) -> bool:
    """Regular session 9:45–16:00 ET, skip first 15 min."""
    et = _et_now(now)
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


def is_moo_submission_window(now: datetime | None = None) -> bool:
    """True if within MOO submission window (e.g. 4:00–9:28 ET)."""
    et = _et_now(now)
    if et.weekday() >= 5:
        return False
    moo_start = et.replace(hour=4, minute=0, second=0, microsecond=0)
    moo_deadline = et.replace(hour=9, minute=28, second=0, microsecond=0)
    return moo_start <= et < moo_deadline


def is_moc_submission_window(now: datetime | None = None) -> bool:
    """True if within MOC submission window (before 15:50 ET)."""
    et = _et_now(now)
    if et.weekday() >= 5:
        return False
    session_start = et.replace(hour=9, minute=30, second=0, microsecond=0)
    moc_deadline = et.replace(hour=15, minute=50, second=0, microsecond=0)
    return session_start <= et < moc_deadline

