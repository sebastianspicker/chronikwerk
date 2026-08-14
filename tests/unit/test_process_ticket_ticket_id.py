"""Verifies ticket-ID extraction accepts only valid integer payload values."""

from __future__ import annotations

import asyncio

import pytest

from chronikwerk.app.jobs import process_ticket as process_ticket_module
from chronikwerk.app.jobs._ticket_pipeline import ProcessTicketResult
from chronikwerk.domain.ticket_id import extract_ticket_id as _extract_ticket_id
from tests.support.process_ticket_helpers import process_ticket_settings


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"ticket": {"id": 123}}, 123),
        ({"ticket": {"id": "123"}}, 123),
        ({"ticket_id": 456}, 456),
        ({"ticket_id": "456"}, 456),
    ],
)
def test_extract_ticket_id_accepts_integer_values(
    payload: dict[str, object], expected: int
) -> None:
    assert _extract_ticket_id(payload) == expected


@pytest.mark.parametrize(
    "payload",
    [
        {"ticket": {"id": True}},
        {"ticket": {"id": False}},
        {"ticket": {"id": 0}},
        {"ticket": {"id": -1}},
        {"ticket": {"id": 1.5}},
        {"ticket_id": True},
        {"ticket_id": 0},
        {"ticket_id": "0"},
        {"ticket_id": "-1"},
        {"ticket_id": 1.5},
        {"ticket_id": "not-a-number"},
    ],
)
def test_extract_ticket_id_rejects_non_integer_values(payload: dict[str, object]) -> None:
    assert _extract_ticket_id(payload) is None


@pytest.mark.parametrize(
    "payload",
    [{}, {"ticket": []}, {"ticket": {"id": "not-an-id"}}, {"ticket_id": False}],
)
def test_process_ticket_records_malformed_or_missing_ticket_ids_as_skipped(
    tmp_path, monkeypatch, payload: dict[str, object]
) -> None:
    recorded_statuses: list[str] = []

    def record_history(_ctx, *, status: str) -> None:
        recorded_statuses.append(status)

    async def ticket_lock_must_not_run(*_args: object) -> bool:
        raise AssertionError("ticket locking must not run without a ticket ID")

    monkeypatch.setattr(process_ticket_module, "_record_history", record_history)
    monkeypatch.setattr(process_ticket_module, "try_acquire_ticket", ticket_lock_must_not_run)

    result = asyncio.run(
        process_ticket_module.process_ticket(
            "delivery-without-ticket", payload, process_ticket_settings(tmp_path)
        )
    )

    assert result == ProcessTicketResult(status="skipped_no_ticket_id", ticket_id=None)
    assert recorded_statuses == ["skipped_no_ticket_id"]


def test_process_ticket_bypasses_delivery_dedupe_without_a_delivery_id(
    tmp_path, monkeypatch
) -> None:
    async def acquire_ticket(*_args: object) -> bool:
        return True

    async def claim_delivery_must_not_run(*_args: object) -> bool:
        raise AssertionError("a missing delivery ID must bypass delivery deduplication")

    async def successful_pipeline(*_args: object, **_kwargs: object) -> ProcessTicketResult:
        return ProcessTicketResult(status="archived", ticket_id=123)

    async def release_ticket_lock(*_args: object) -> None:
        return None

    monkeypatch.setattr(process_ticket_module, "try_acquire_ticket", acquire_ticket)
    monkeypatch.setattr(process_ticket_module, "try_claim_delivery_id", claim_delivery_must_not_run)
    monkeypatch.setattr(process_ticket_module, "_process_ticket_with_client", successful_pipeline)
    monkeypatch.setattr(process_ticket_module, "release_ticket", release_ticket_lock)

    result = asyncio.run(
        process_ticket_module.process_ticket(
            None, {"ticket_id": 123}, process_ticket_settings(tmp_path)
        )
    )

    assert result == ProcessTicketResult(status="archived", ticket_id=123)


def test_process_ticket_contains_ticket_lock_release_failures(tmp_path, monkeypatch) -> None:
    release_events: list[str] = []

    class CapturingLog:
        def exception(self, event: str, **_kwargs: object) -> None:
            release_events.append(event)

    async def acquire_ticket(*_args: object) -> bool:
        return True

    async def successful_pipeline(*_args: object, **_kwargs: object) -> ProcessTicketResult:
        return ProcessTicketResult(status="archived", ticket_id=456)

    async def fail_release_ticket(*_args: object) -> None:
        raise RuntimeError("release backend unavailable")

    monkeypatch.setattr(process_ticket_module, "log", CapturingLog())
    monkeypatch.setattr(process_ticket_module, "try_acquire_ticket", acquire_ticket)
    monkeypatch.setattr(process_ticket_module, "_process_ticket_with_client", successful_pipeline)
    monkeypatch.setattr(process_ticket_module, "release_ticket", fail_release_ticket)

    result = asyncio.run(
        process_ticket_module.process_ticket(
            "delivery-456", {"ticket_id": 456}, process_ticket_settings(tmp_path)
        )
    )

    assert result == ProcessTicketResult(status="archived", ticket_id=456)
    assert release_events == ["process_ticket.release_ticket_failed"]
