from __future__ import annotations

from typing import Any, cast

import pytest

from test.unit.test_redis_queue import (
    _FakeCounter,
    _FakeRedis,
    check,
    make_settings,
    redis_queue,
)
from zammad_pdf_archiver.app.jobs._queue_types import _QueueEnvelope
from zammad_pdf_archiver.app.jobs.process_ticket import ProcessTicketResult

REDIS_WORKFLOW = {"execution_backend": "redis_queue", "redis_url": "redis://localhost:6379"}


class _CapturingLog:
    def __init__(self) -> None:
        self.warning_events: list[tuple[str, dict[str, object]]] = []

    def warning(self, event: str, **kwargs: object) -> None:
        self.warning_events.append((event, kwargs))


def _settings(tmp_path, *, workflow: dict[str, object] | None = None):
    return make_settings(str(tmp_path), overrides={"workflow": workflow or REDIS_WORKFLOW})


def _envelope(message_id: str, *, delivery_id: str = "d-5") -> _QueueEnvelope:
    return _QueueEnvelope(
        message_id=message_id,
        payload={"ticket_id": 99},
        delivery_id=delivery_id,
        attempt=0,
        not_before_ts=0.0,
        last_error=None,
    )


def _ack(settings, message_id: str):
    return (settings.workflow.queue_stream, settings.workflow.queue_group, message_id)


def _deleted(settings, message_id: str):
    return (settings.workflow.queue_stream, message_id)


def _stub_process_result(monkeypatch, result: ProcessTicketResult) -> None:
    async def _stub_process_ticket(*args, **kwargs):  # noqa: ANN002, ANN003
        return result

    monkeypatch.setattr(redis_queue, "process_ticket", _stub_process_ticket)


def _handle(fake: _FakeRedis, settings, envelope: _QueueEnvelope) -> float:
    return redis_queue.asyncio.run(
        redis_queue._handle_envelope(fake, settings=settings, envelope=envelope)  # noqa: SLF001
    )


def test_handle_envelope_success_acks_and_increments(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    fake = _FakeRedis()
    processed_counter = _FakeCounter()

    _stub_process_result(
        monkeypatch,
        ProcessTicketResult(status="processed", ticket_id=99, message="done"),
    )
    monkeypatch.setattr(redis_queue, "queue_processed_total", processed_counter)
    result = _handle(fake, settings, _envelope("5-0"))

    check(not not result == 0.0, "assertion failed")
    check(not not fake.acked == [_ack(settings, "5-0")], "assertion failed")
    check(not not fake.deleted[-1] == _deleted(settings, "5-0"), "assertion failed")
    check(not not processed_counter.count == 1, "assertion failed")


def test_handle_envelope_logs_lock_release_failure(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    fake = _FakeRedis()
    capturing_log = _CapturingLog()

    _stub_process_result(
        monkeypatch,
        ProcessTicketResult(
            status="processed",
            ticket_id=99,
            message="done",
            lock_release_failed=True,
        ),
    )

    monkeypatch.setattr(redis_queue, "log", capturing_log)
    _handle(fake, settings, _envelope("5-lock", delivery_id="d-lock"))

    check(
        not not capturing_log.warning_events
        == [
            (
                "queue.worker.ticket_lock_release_failed",
                {"ticket_id": 99, "message_id": "5-lock", "delivery_id": "d-lock"},
            )
        ],
        "assertion failed",
    )
    check(not not fake.acked == [_ack(settings, "5-lock")], "assertion failed")


def test_handle_envelope_counts_and_logs_history_record_failure(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    fake = _FakeRedis()
    failed_counter = _FakeCounter()
    capturing_log = _CapturingLog()

    _stub_process_result(
        monkeypatch,
        ProcessTicketResult(
            status="processed",
            ticket_id=99,
            message="done",
            history_recorded=False,
        ),
    )

    monkeypatch.setattr(redis_queue, "history_record_failed_total", failed_counter)
    monkeypatch.setattr(redis_queue, "log", capturing_log)
    _handle(fake, settings, _envelope("5-history", delivery_id="d-history"))

    check(not not failed_counter.count == 1, "assertion failed")
    check(
        not not capturing_log.warning_events
        == [
            (
                "process_ticket.history_not_recorded",
                {"ticket_id": 99, "message_id": "5-history", "delivery_id": "d-history"},
            )
        ],
        "assertion failed",
    )
    check(not not fake.acked == [_ack(settings, "5-history")], "assertion failed")


@pytest.mark.parametrize(
    "status",
    ["processed_done_update_failed", "processed_acknowledgement_failed"],
)
def test_handle_envelope_partial_status_acks_without_processed_metric(
    monkeypatch, tmp_path, status: str
) -> None:
    settings = _settings(tmp_path)
    fake = _FakeRedis()
    partial_counter = _FakeCounter()
    processed_counter = _FakeCounter()

    _stub_process_result(
        monkeypatch,
        ProcessTicketResult(status=cast(Any, status), ticket_id=99, message="partial"),
    )
    monkeypatch.setattr(redis_queue, "queue_partial_total", partial_counter)
    monkeypatch.setattr(redis_queue, "queue_processed_total", processed_counter)

    result = _handle(fake, settings, _envelope("5-1"))

    check(not not result == 0.0, "assertion failed")
    check(not not fake.acked == [_ack(settings, "5-1")], "assertion failed")
    check(not not fake.deleted[-1] == _deleted(settings, "5-1"), "assertion failed")
    check(not not fake.xadds == [], "assertion failed")
    check(not not partial_counter.count == 1, "assertion failed")
    check(not not processed_counter.count == 0, "assertion failed")


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        ("skipped_no_ticket_id", "no_ticket_id"),
        ("skipped_not_triggered", "not_triggered"),
        ("skipped_in_flight", "in_flight"),
        ("skipped_idempotency", "idempotency"),
    ],
)
def test_handle_envelope_skipped_status_acks_with_skip_metric(
    monkeypatch, tmp_path, status: str, reason: str
) -> None:
    settings = _settings(tmp_path)
    fake = _FakeRedis()
    skipped_counter = _FakeCounter()
    processed_counter = _FakeCounter()

    _stub_process_result(
        monkeypatch,
        ProcessTicketResult(status=cast(Any, status), ticket_id=99, message="skipped"),
    )
    monkeypatch.setattr(redis_queue, "queue_skipped_total", skipped_counter)
    monkeypatch.setattr(redis_queue, "queue_processed_total", processed_counter)

    result = _handle(fake, settings, _envelope("5-2"))

    check(not not result == 0.0, "assertion failed")
    check(not not fake.acked == [_ack(settings, "5-2")], "assertion failed")
    check(not not fake.deleted[-1] == _deleted(settings, "5-2"), "assertion failed")
    check(not not fake.xadds == [], "assertion failed")
    check(not not skipped_counter.count == 1, "assertion failed")
    check(not not skipped_counter.label_calls == [{"reason": reason}], "assertion failed")
    check(not not processed_counter.count == 0, "assertion failed")


def test_handle_envelope_unknown_status_moves_to_dlq(monkeypatch, tmp_path) -> None:
    settings = _settings(
        tmp_path,
        workflow={**REDIS_WORKFLOW, "queue_dlq_stream": "zammad:jobs:dlq"},
    )
    fake = _FakeRedis()
    unknown_counter = _FakeCounter()
    processed_counter = _FakeCounter()

    _stub_process_result(
        monkeypatch,
        ProcessTicketResult(status=cast(Any, "ok"), ticket_id=99, message="unexpected"),
    )
    monkeypatch.setattr(redis_queue, "queue_unknown_status_total", unknown_counter)
    monkeypatch.setattr(redis_queue, "queue_processed_total", processed_counter)

    result = _handle(fake, settings, _envelope("5-3"))

    check(not not result == 0.0, "assertion failed")
    dlq_entries = [
        fields for stream, fields in fake.xadds if stream == settings.workflow.queue_dlq_stream
    ]
    check(not not dlq_entries, "assertion failed")
    check(not not dlq_entries[0]["reason"] == "unknown_status", "assertion failed")
    check(not not dlq_entries[0]["error"] == "unexpected", "assertion failed")
    check(not not fake.acked == [_ack(settings, "5-3")], "assertion failed")
    check(not not fake.deleted[-1] == _deleted(settings, "5-3"), "assertion failed")
    check(not not unknown_counter.count == 1, "assertion failed")
    check(not not processed_counter.count == 0, "assertion failed")
