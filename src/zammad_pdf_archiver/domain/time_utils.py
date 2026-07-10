"""Project module."""
from __future__ import annotations

from datetime import UTC, datetime


def now_utc() -> datetime:
    """Implement the now utc operation."""
    return datetime.now(UTC)


def format_timestamp_utc(dt: datetime) -> str:
    """Implement the format timestamp utc operation."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    dt_utc = dt.astimezone(UTC)
    return dt_utc.replace(microsecond=0).isoformat().replace("+00:00", "Z")
