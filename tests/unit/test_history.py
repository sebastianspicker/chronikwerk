"""Verifies durable job-history ordering, filtering, retention, and error classification."""

from __future__ import annotations

from chronikwerk.app.jobs.history import read_history, record_history_event, reset_for_tests


def test_history_records_and_reads_newest_first(tmp_path) -> None:
    reset_for_tests()
    record_history_event(status="ok", ticket_id=1, message="first")
    record_history_event(status="error", ticket_id=2, message="second")

    entries = read_history(limit=10)

    assert [entry["ticket_id"] for entry in entries] == [2, 1]


def test_history_filters_ticket_id(tmp_path) -> None:
    reset_for_tests()
    record_history_event(status="ok", ticket_id=1)
    record_history_event(status="ok", ticket_id=2)

    entries = read_history(limit=10, ticket_id=1)

    assert [entry["ticket_id"] for entry in entries] == [1]


def test_history_ids_remain_unique_after_retention_rollover() -> None:
    reset_for_tests()
    for index in range(5001):
        record_history_event(status="processed", ticket_id=index)
    entries = read_history(limit=5000)
    ids = [int(item["id"]) for item in entries]
    assert len(ids) == 5000
    assert len(set(ids)) == 5000
    assert ids[0] == 5001
    assert ids[-1] == 2
    reset_for_tests()


def test_history_status_filter_groups_failure_classifications() -> None:
    reset_for_tests()
    record_history_event(status="failed_transient", ticket_id=1)
    record_history_event(status="failed_permanent", ticket_id=2)
    record_history_event(status="processed", ticket_id=3)

    entries = read_history(limit=10, statuses={"failed"})

    assert [entry["ticket_id"] for entry in entries] == [2, 1]
