"""Verify bounded history reads, filters, and cursors through the public interface."""

from __future__ import annotations

from chronikwerk.operations.history import JobHistory


def _history() -> JobHistory:
    """Record a small mixed history for two tickets."""
    history = JobHistory()
    history.record("failed_old", 42)
    history.record("processed", 42)
    history.record("failed_recent", 42)
    history.record("accepted", 7)
    return history


def test_zero_or_negative_limit_returns_no_entries() -> None:
    """A non-positive limit yields an empty page even when entries exist."""
    history = _history()

    assert history.read(0) == []
    assert history.read(-5) == []


def test_limit_returns_newest_matching_entries_first() -> None:
    """Status prefixes match suffixed statuses and the limit keeps only the newest."""
    history = _history()

    assert [item["id"] for item in history.read(1, statuses={"failed"})] == ["3"]
    assert [item["id"] for item in history.read(10, statuses={"failed"})] == ["3", "1"]
    assert [item["id"] for item in history.read(2)] == ["4", "3"]


def test_ticket_and_cursor_filters_combine() -> None:
    """Ticket filters and before-id cursors narrow pages without reordering them."""
    history = _history()

    assert [item["id"] for item in history.read(10, 42)] == ["3", "2", "1"]
    assert [item["id"] for item in history.read(10, 42, before_id=3)] == ["2", "1"]
    assert history.read(10, 7, before_id=4) == []


def test_capacity_evicts_oldest_entries_and_bounds_limit() -> None:
    """The bounded history keeps only the newest entries and caps the requested limit."""
    history = JobHistory(max_entries=2)
    for ticket_id in (1, 2, 3):
        history.record("processed", ticket_id)

    assert [item["id"] for item in history.read(100)] == ["3", "2"]
