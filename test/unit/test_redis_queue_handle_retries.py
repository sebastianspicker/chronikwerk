from __future__ import annotations

import time

import pytest

from test.unit.test_redis_queue import (
    _FakeRedis,
    check,
    make_settings,
    redis_queue,
)
from zammad_pdf_archiver.app.jobs._queue_types import _QueueEnvelope
from zammad_pdf_archiver.app.jobs.process_ticket import ProcessTicketResult


def test_handle_envelope_transient_requeues(monkeypatch, tmp_path) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={
            "workflow": {
                "execution_backend": "redis_queue",
                "redis_url": "redis://localhost/0",
                "queue_retry_max_attempts": 2,
                "queue_retry_backoff_seconds": 1.0,
            }
        },
    )
    fake = _FakeRedis()

    async def _stub_process_ticket(delivery_id, payload, settings):  # noqa: ANN001, ARG001
        return ProcessTicketResult(
            status="failed_transient",
            ticket_id=payload.get("ticket_id"),
            message="tmp",
        )

    async def _stub_enqueue_ticket_job(  # noqa: ANN001
        *,
        delivery_id,
        payload,
        settings,
        attempt,
        not_before_ts,
        last_error,
    ) -> str:
        fields = {
            "payload_json": "{}",
            "delivery_id": delivery_id or "",
            "attempt": str(attempt),
            "not_before_ts": str(not_before_ts),
            "last_error": last_error or "",
        }
        return await fake.xadd(settings.workflow.queue_stream, fields)

    monkeypatch.setattr(redis_queue, "process_ticket", _stub_process_ticket)
    monkeypatch.setattr(redis_queue, "enqueue_ticket_job", _stub_enqueue_ticket_job)
    envelope = _QueueEnvelope(
        message_id="1-0",
        payload={"ticket_id": 123},
        delivery_id="d-1",
        attempt=0,
        not_before_ts=0.0,
        last_error=None,
    )

    redis_queue.asyncio.run(
        redis_queue._handle_envelope(fake, settings=settings, envelope=envelope)  # noqa: SLF001
    )

    check(
        not not any((stream == settings.workflow.queue_stream for stream, _ in fake.xadds)),
        "assertion failed",
    )
    retry_entry = next(
        fields for stream, fields in fake.xadds if stream == settings.workflow.queue_stream
    )
    check(not not retry_entry["attempt"] == "1", "assertion failed")
    check(not not float(retry_entry["not_before_ts"]) >= time.time() - 0.5, "assertion failed")
    check(
        not not fake.acked
        == [(settings.workflow.queue_stream, settings.workflow.queue_group, "1-0")],
        "assertion failed",
    )
    check(not not fake.deleted[-1] == (settings.workflow.queue_stream, "1-0"), "assertion failed")


def test_handle_envelope_transient_requeues_and_preserves_original_when_ack_fails(
    monkeypatch, tmp_path
) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={
            "workflow": {
                "execution_backend": "redis_queue",
                "redis_url": "redis://localhost/0",
                "queue_retry_max_attempts": 2,
                "queue_retry_backoff_seconds": 1.0,
            }
        },
    )
    fake = _FakeRedis()

    async def _stub_process_ticket(delivery_id, payload, settings):  # noqa: ANN001, ARG001
        return ProcessTicketResult(
            status="failed_transient",
            ticket_id=payload.get("ticket_id"),
            message="tmp",
        )

    async def _stub_enqueue_ticket_job(  # noqa: ANN001
        *,
        delivery_id,
        payload,
        settings,
        attempt,
        not_before_ts,
        last_error,
    ) -> str:
        fields = {
            "payload_json": "{}",
            "delivery_id": delivery_id or "",
            "attempt": str(attempt),
            "not_before_ts": str(not_before_ts),
            "last_error": last_error or "",
        }
        return await fake.xadd(settings.workflow.queue_stream, fields)

    async def _failing_xack(stream: str, group: str, message_id: str) -> int:  # noqa: ARG001
        raise RuntimeError("ack failed")

    monkeypatch.setattr(redis_queue, "process_ticket", _stub_process_ticket)
    monkeypatch.setattr(redis_queue, "enqueue_ticket_job", _stub_enqueue_ticket_job)
    fake.xack = _failing_xack  # type: ignore[method-assign]
    envelope = _QueueEnvelope(
        message_id="1-0",
        payload={"ticket_id": 123},
        delivery_id="d-1",
        attempt=0,
        not_before_ts=0.0,
        last_error=None,
    )

    with pytest.raises(RuntimeError, match="ack failed"):
        redis_queue.asyncio.run(
            redis_queue._handle_envelope(fake, settings=settings, envelope=envelope)  # noqa: SLF001
        )

    check(
        not not any((stream == settings.workflow.queue_stream for stream, _ in fake.xadds)),
        "assertion failed",
    )
    check(not not (settings.workflow.queue_stream, "1-0") not in fake.deleted, "assertion failed")


def test_handle_envelope_permanent_moves_to_dlq(monkeypatch, tmp_path) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={
            "workflow": {
                "execution_backend": "redis_queue",
                "redis_url": "redis://localhost/0",
                "queue_dlq_stream": "zammad:jobs:dlq",
            }
        },
    )
    fake = _FakeRedis()

    async def _stub_process_ticket(delivery_id, payload, settings):  # noqa: ANN001, ARG001
        return ProcessTicketResult(
            status="failed_permanent",
            ticket_id=payload.get("ticket_id"),
            message="perm",
        )

    monkeypatch.setattr(redis_queue, "process_ticket", _stub_process_ticket)
    envelope = _QueueEnvelope(
        message_id="2-0",
        payload={"ticket_id": 321},
        delivery_id="d-2",
        attempt=0,
        not_before_ts=0.0,
        last_error=None,
    )

    redis_queue.asyncio.run(
        redis_queue._handle_envelope(fake, settings=settings, envelope=envelope)  # noqa: SLF001
    )

    check(
        not not any((stream == settings.workflow.queue_dlq_stream for stream, _ in fake.xadds)),
        "assertion failed",
    )
    check(
        not not fake.acked
        == [(settings.workflow.queue_stream, settings.workflow.queue_group, "2-0")],
        "assertion failed",
    )
    check(not not fake.deleted[-1] == (settings.workflow.queue_stream, "2-0"), "assertion failed")


def test_drain_dlq_respects_limit(monkeypatch, tmp_path) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={
            "workflow": {"execution_backend": "redis_queue", "redis_url": "redis://localhost/0"}
        },
    )
    fake = _FakeRedis(
        dlq_entries=[
            ("1-0", {"payload_json": "{}"}),
            ("2-0", {"payload_json": "{}"}),
            ("3-0", {"payload_json": "{}"}),
        ]
    )

    async def _stub_get_redis(_settings):  # noqa: ANN001
        return fake

    monkeypatch.setattr(redis_queue, "_get_redis", _stub_get_redis)
    drained = redis_queue.asyncio.run(redis_queue.drain_dlq(settings, limit=2))
    check(not not drained == {"selected": 2, "deleted": 2, "not_deleted": 0}, "assertion failed")
    check(
        not not fake.deleted
        == [
            (settings.workflow.queue_dlq_stream, "1-0"),
            (settings.workflow.queue_dlq_stream, "2-0"),
        ],
        "assertion failed",
    )


def test_drain_dlq_reports_partial_delete_results(monkeypatch, tmp_path) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={
            "workflow": {"execution_backend": "redis_queue", "redis_url": "redis://localhost/0"}
        },
    )
    fake = _FakeRedis(
        dlq_entries=[
            ("1-0", {"payload_json": "{}"}),
            ("2-0", {"payload_json": "{}"}),
            ("3-0", {"payload_json": "{}"}),
        ],
        pipeline_results=[1, 0, 1],
    )

    async def _stub_get_redis(_settings):  # noqa: ANN001
        return fake

    monkeypatch.setattr(redis_queue, "_get_redis", _stub_get_redis)
    drained = redis_queue.asyncio.run(redis_queue.drain_dlq(settings, limit=10))
    check(not not drained == {"selected": 3, "deleted": 2, "not_deleted": 1}, "assertion failed")
    check(
        not not fake.deleted
        == [
            (settings.workflow.queue_dlq_stream, "1-0"),
            (settings.workflow.queue_dlq_stream, "3-0"),
        ],
        "assertion failed",
    )


def test_drain_dlq_pipeline_exception_propagates(monkeypatch, tmp_path) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={
            "workflow": {"execution_backend": "redis_queue", "redis_url": "redis://localhost/0"}
        },
    )
    fake = _FakeRedis(
        dlq_entries=[("1-0", {"payload_json": "{}"})],
        pipeline_error=RuntimeError("redis pipeline failed"),
    )

    async def _stub_get_redis(_settings):  # noqa: ANN001
        return fake

    monkeypatch.setattr(redis_queue, "_get_redis", _stub_get_redis)
    with pytest.raises(RuntimeError, match="redis pipeline failed"):
        redis_queue.asyncio.run(redis_queue.drain_dlq(settings, limit=10))
    check(not not fake.deleted == [], "assertion failed")


def test_handle_envelope_not_before_is_deferred_without_reenqueue(monkeypatch, tmp_path) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={
            "workflow": {
                "execution_backend": "redis_queue",
                "redis_url": "redis://localhost/0",
            }
        },
    )
    fake = _FakeRedis()

    async def _stub_process_ticket(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("process_ticket should not run before not_before_ts")

    monkeypatch.setattr(redis_queue, "process_ticket", _stub_process_ticket)
    envelope = _QueueEnvelope(
        message_id="3-0",
        payload={"ticket_id": 123},
        delivery_id="d-3",
        attempt=1,
        not_before_ts=time.time() + 60,
        last_error="tmp",
    )

    defer_seconds = redis_queue.asyncio.run(
        redis_queue._handle_envelope(fake, settings=settings, envelope=envelope)
    )

    check(not not defer_seconds > 0, "assertion failed")
    check(not not fake.xadds == [], "assertion failed")
    check(not not fake.acked == [], "assertion failed")
    check(not not fake.deleted == [], "assertion failed")


def test_handle_envelope_retry_exhausted_moves_to_dlq(monkeypatch, tmp_path) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={
            "workflow": {
                "execution_backend": "redis_queue",
                "redis_url": "redis://localhost:6379",
                "queue_retry_max_attempts": 2,
                "queue_dlq_stream": "zammad:jobs:dlq",
            }
        },
    )
    fake = _FakeRedis()

    async def _stub_process_ticket(*args, **kwargs):  # noqa: ANN002, ANN003
        return ProcessTicketResult(status="failed_transient", ticket_id=42, message="transient")

    monkeypatch.setattr(redis_queue, "process_ticket", _stub_process_ticket)
    envelope = _QueueEnvelope(
        message_id="1-0",
        payload={"ticket_id": 42},
        delivery_id="dlv1",
        attempt=2,  # >= max_attempts=2
        not_before_ts=0.0,
        last_error=None,
    )
    redis_queue.asyncio.run(
        redis_queue._handle_envelope(fake, settings=settings, envelope=envelope)  # noqa: SLF001
    )
    dlq_entries = [e for stream, e in fake.xadds if "dlq" in stream]
    check(
        not not any(e.get("reason") == "retry_exhausted" for e in dlq_entries), "assertion failed"
    )

