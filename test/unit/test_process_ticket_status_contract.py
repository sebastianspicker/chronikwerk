from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast, get_args

import pytest

from test.support.checks import check
from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.jobs import redis_queue
from zammad_pdf_archiver.app.jobs._queue_types import _QueueEnvelope
from zammad_pdf_archiver.app.jobs.process_ticket import (
    ProcessTicketResult,
    ProcessTicketStatus,
)


@dataclass
class _FakeRedis:
    xadds: list[tuple[str, dict[str, str]]] = field(default_factory=list)
    acked: list[tuple[str, str, str]] = field(default_factory=list)
    deleted: list[tuple[str, str]] = field(default_factory=list)

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        self.xadds.append((stream, fields))
        return f"{len(self.xadds)}-0"

    async def xack(self, stream: str, group: str, message_id: str) -> int:
        self.acked.append((stream, group, message_id))
        return 1

    async def xdel(self, stream: str, message_id: str) -> int:
        self.deleted.append((stream, message_id))
        return 1


ACK_ONLY_STATUSES: tuple[ProcessTicketStatus, ...] = (
    "processed",
    "processed_done_update_failed",
    "processed_acknowledgement_failed",
    "skipped_no_ticket_id",
    "skipped_not_triggered",
    "skipped_in_flight",
    "skipped_idempotency",
)
KNOWN_PROCESS_TICKET_STATUSES = get_args(ProcessTicketStatus)


def _settings(tmp_path: Path) -> Any:
    return make_settings(
        str(tmp_path),
        overrides={
            "workflow": {
                "execution_backend": "redis_queue",
                "redis_url": "redis://localhost:6379",
                "queue_retry_max_attempts": 2,
                "queue_retry_backoff_seconds": 1.0,
                "queue_dlq_stream": "zammad:jobs:dlq",
            }
        },
    )


def _envelope() -> _QueueEnvelope:
    return _QueueEnvelope(
        message_id="1-0",
        payload={"ticket_id": 42},
        delivery_id="d-1",
        attempt=0,
        not_before_ts=0.0,
        last_error=None,
    )


def _stub_process_ticket_status(
    monkeypatch: pytest.MonkeyPatch,
    *,
    status: ProcessTicketStatus | str,
    message: str | None = None,
) -> None:
    async def _stub_process_ticket(
        delivery_id: str | None,  # noqa: ARG001
        payload: dict[str, Any],
        settings: Any,  # noqa: ARG001
    ) -> ProcessTicketResult:
        return ProcessTicketResult(
            status=cast(Any, status),
            ticket_id=payload.get("ticket_id"),
            message=status if message is None else message,
        )

    monkeypatch.setattr(redis_queue, "process_ticket", _stub_process_ticket)


def _handle_status(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    status: ProcessTicketStatus | str,
    message: str | None = None,
) -> tuple[Any, _FakeRedis, float]:
    settings = _settings(tmp_path)
    fake = _FakeRedis()
    _stub_process_ticket_status(monkeypatch, status=status, message=message)

    result = asyncio.run(
        redis_queue._handle_envelope(fake, settings=settings, envelope=_envelope())  # noqa: SLF001
    )
    return settings, fake, result


def _assert_acked_and_deleted(fake: _FakeRedis, settings: Any) -> None:
    check(
        not not fake.acked
        == [(settings.workflow.queue_stream, settings.workflow.queue_group, "1-0")],
        "assertion failed",
    )
    check(not not fake.deleted == [(settings.workflow.queue_stream, "1-0")], "assertion failed")


def test_contract_statuses_match_process_ticket_literal() -> None:
    check(
        not not set(KNOWN_PROCESS_TICKET_STATUSES)
        == {*ACK_ONLY_STATUSES, "failed_transient", "failed_permanent"},
        "assertion failed",
    )


@pytest.mark.parametrize("status", ACK_ONLY_STATUSES)
def test_known_nonfailed_statuses_ack_without_dlq(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    status: ProcessTicketStatus,
) -> None:
    settings, fake, result = _handle_status(monkeypatch, tmp_path, status=status)

    check(not not result == 0.0, "assertion failed")
    _assert_acked_and_deleted(fake, settings)
    check(not not fake.xadds == [], "assertion failed")


def test_failed_transient_status_requeues_and_acks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    fake = _FakeRedis()
    _stub_process_ticket_status(monkeypatch, status="failed_transient", message="retry me")

    async def _stub_enqueue_ticket_job(
        *,
        delivery_id: str | None,
        payload: dict[str, Any],
        settings: Any,
        attempt: int,
        not_before_ts: float,
        last_error: str | None,
    ) -> str:
        fields = {
            "payload_json": json.dumps(payload, separators=(",", ":"), sort_keys=True),
            "delivery_id": delivery_id or "",
            "attempt": str(attempt),
            "not_before_ts": str(not_before_ts),
            "last_error": last_error or "",
        }
        return await fake.xadd(settings.workflow.queue_stream, fields)

    monkeypatch.setattr(redis_queue, "enqueue_ticket_job", _stub_enqueue_ticket_job)

    result = asyncio.run(
        redis_queue._handle_envelope(fake, settings=settings, envelope=_envelope())  # noqa: SLF001
    )

    check(not not result == 0.0, "assertion failed")
    _assert_acked_and_deleted(fake, settings)
    check(not not fake.xadds[0][0] == settings.workflow.queue_stream, "assertion failed")
    check(not not fake.xadds[0][1]["attempt"] == "1", "assertion failed")
    check(not not fake.xadds[0][1]["last_error"] == "retry me", "assertion failed")


def test_failed_permanent_status_moves_to_dlq_and_acks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings, fake, result = _handle_status(
        monkeypatch,
        tmp_path,
        status="failed_permanent",
        message="permanent",
    )

    check(not not result == 0.0, "assertion failed")
    _assert_acked_and_deleted(fake, settings)
    check(not not fake.xadds[0][0] == settings.workflow.queue_dlq_stream, "assertion failed")
    check(not not fake.xadds[0][1]["reason"] == "permanent_error", "assertion failed")


def test_unknown_status_moves_to_dlq_with_unknown_status_reason(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings, fake, result = _handle_status(monkeypatch, tmp_path, status="xyzzy", message="")

    check(not not result == 0.0, "assertion failed")
    _assert_acked_and_deleted(fake, settings)
    check(not not fake.xadds[0][0] == settings.workflow.queue_dlq_stream, "assertion failed")
    check(not not fake.xadds[0][1]["reason"] == "unknown_status", "assertion failed")
    check(
        not not fake.xadds[0][1]["error"] == "unknown process_ticket status: xyzzy",
        "assertion failed",
    )
