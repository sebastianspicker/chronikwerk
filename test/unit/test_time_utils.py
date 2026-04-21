from __future__ import annotations

from datetime import UTC, datetime, timezone

from zammad_pdf_archiver.domain.time_utils import format_timestamp_utc


def test_format_timestamp_utc_naive_datetime_treated_as_utc() -> None:
    """A naive datetime (no tzinfo) should be treated as UTC."""
    naive = datetime(2024, 6, 15, 12, 30, 45)  # noqa: DTZ001
    assert naive.tzinfo is None

    result = format_timestamp_utc(naive)
    assert result == "2024-06-15T12:30:45Z"


def test_format_timestamp_utc_aware_datetime() -> None:
    """An aware UTC datetime should format correctly."""
    aware = datetime(2024, 6, 15, 12, 30, 45, tzinfo=UTC)
    result = format_timestamp_utc(aware)
    assert result == "2024-06-15T12:30:45Z"


def test_format_timestamp_utc_non_utc_timezone() -> None:
    """A datetime in a non-UTC timezone should be converted to UTC."""
    cet = timezone(offset=__import__("datetime").timedelta(hours=2))
    aware_cet = datetime(2024, 6, 15, 14, 30, 45, tzinfo=cet)
    result = format_timestamp_utc(aware_cet)
    assert result == "2024-06-15T12:30:45Z"


def test_format_timestamp_utc_truncates_microseconds() -> None:
    """Microseconds should be truncated in the output."""
    dt = datetime(2024, 6, 15, 12, 30, 45, 123456, tzinfo=UTC)
    result = format_timestamp_utc(dt)
    assert result == "2024-06-15T12:30:45Z"
