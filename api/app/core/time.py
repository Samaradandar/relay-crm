# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""UTC storage, IST display, IST month boundaries."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def utc_now() -> datetime:
    return datetime.now(UTC)


def ensure_utc(dt: datetime) -> datetime:
    """Naive datetimes are rejected: storing local time by accident is a bug."""
    if dt.tzinfo is None:
        raise ValueError("naive datetime not allowed; use timezone-aware values")
    return dt.astimezone(UTC)


def to_ist(dt: datetime) -> datetime:
    return ensure_utc(dt).astimezone(IST)


def format_ist(dt: datetime) -> str:
    return to_ist(dt).strftime("%d %b %Y, %H:%M:%S IST")


def ist_month_bounds(dt: datetime) -> tuple[datetime, datetime]:
    """[start, end) of the IST calendar month containing dt, returned in UTC."""
    local = to_ist(dt)
    start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start.astimezone(UTC), end.astimezone(UTC)
