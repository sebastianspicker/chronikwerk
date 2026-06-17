from __future__ import annotations

from zammad_pdf_archiver.app.jobs.history import read_history, record_history_event, reset_for_tests


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
