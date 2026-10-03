"""Verify process-local exclusion and delivery deduplication through the public processor."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest

from chronikwerk.archiving.options import ArchiveRuntimeOptions
from chronikwerk.archiving.processor import build_ticket_processor
from chronikwerk.archiving.workflow import ArchiveOutcome
from chronikwerk.operations.guards import TicketGuards
from chronikwerk.operations.history import JobHistory
from chronikwerk.operations.job import TicketJob
from chronikwerk.zammad.dto import Ticket
from chronikwerk.zammad.errors import ServerError
from tests.support.fake_zammad import FakeZammad

_OPTIONS = cast(
    ArchiveRuntimeOptions,
    SimpleNamespace(workflow=SimpleNamespace(trigger_tag="pdf:sign", require_trigger_tag=True)),
)


class _BorrowedZammad(FakeZammad):
    """Fake client that fails if the processor tries to own its lifecycle."""

    async def __aenter__(self) -> Any:
        """Reject entering a caller-owned client."""
        pytest.fail("caller-owned client must not be entered by the processor")

    async def __aexit__(self, *_args: object) -> None:
        """Reject exiting a caller-owned client."""
        pytest.fail("caller-owned client must not be closed by the processor")

    async def aclose(self) -> None:
        """Reject closing a caller-owned client."""
        pytest.fail("caller-owned client must not be closed by the processor")


class _FailingZammad(FakeZammad):
    """Fake client whose ticket fetch fails permanently."""

    async def get_ticket(self, ticket_id: int) -> Ticket:
        """Record the fetch and fail like a rejected request."""
        self.calls.append(("get_ticket",))
        raise ValueError("synthetic permanent failure")


class _FailingTagFetchZammad(FakeZammad):
    """Fake client whose tag fetch fails transiently."""

    async def list_tags(self, _ticket_id: int):
        """Record the fetch and fail like an unavailable upstream."""
        self.calls.append(("list_tags",))
        raise ServerError("synthetic transient failure")


def _job(delivery_id: str | None = None) -> TicketJob:
    """Build a ticket-42 job with stable request metadata."""
    return TicketJob(
        ticket_id=42,
        payload={"ticket_id": 42},
        delivery_id=delivery_id,
        request_id="request-42",
    )


def _run(
    fake: FakeZammad, guards: TicketGuards, history: JobHistory, *jobs: TicketJob
) -> list[ArchiveOutcome]:
    """Run jobs sequentially through one built processor."""
    process = build_ticket_processor(_OPTIONS, client=fake.client, guards=guards, history=history)

    async def scenario() -> list[ArchiveOutcome]:
        return [await process(job) for job in jobs]

    return asyncio.run(scenario())


def test_seen_delivery_skips_without_calling_zammad_and_releases_ticket() -> None:
    """A previously claimed delivery is skipped, recorded, and leaves the ticket free."""
    fake = FakeZammad(tags=["pdf:sign"])
    guards = TicketGuards(delivery_id_ttl_seconds=120)
    history = JobHistory()
    assert guards.try_claim_delivery("delivery-42")

    (outcome,) = _run(fake, guards, history, _job("delivery-42"))

    assert outcome.status == "skipped_idempotency"
    assert outcome.ticket_id == 42
    assert fake.calls == []
    assert [
        (item["status"], item["ticket_id"], item["delivery_id"], item["request_id"])
        for item in reversed(history.read(10))
    ] == [
        ("running", 42, "delivery-42", "request-42"),
        ("skipped_idempotency", 42, "delivery-42", "request-42"),
    ]
    assert guards.try_acquire_ticket(42)


def test_same_delivery_is_claimed_once_until_the_ttl_expires() -> None:
    """The configured TTL bounds suppression; an expired delivery is processed again."""
    now = [0.0]
    fake = FakeZammad(tags=["other"])
    guards = TicketGuards(delivery_id_ttl_seconds=60, now=lambda: now[0])
    history = JobHistory()

    first, second = _run(fake, guards, history, _job("d-1"), _job("d-1"))
    now[0] = 60
    (third,) = _run(fake, guards, history, _job("d-1"))

    assert [first.status, second.status, third.status] == [
        "skipped_not_triggered",
        "skipped_idempotency",
        "skipped_not_triggered",
    ]
    assert fake.names.count("get_ticket") == 2


def test_disabled_deduplication_processes_repeated_deliveries() -> None:
    """A non-positive TTL disables delivery claims."""
    fake = FakeZammad(tags=["other"])
    guards = TicketGuards(delivery_id_ttl_seconds=0)

    outcomes = _run(fake, guards, JobHistory(), _job("d-1"), _job("d-1"))

    assert [outcome.status for outcome in outcomes] == ["skipped_not_triggered"] * 2


def test_in_flight_ticket_is_skipped_without_calling_zammad() -> None:
    """A ticket already held by another job is skipped and stays held."""
    fake = FakeZammad(tags=["pdf:sign"])
    guards = TicketGuards(delivery_id_ttl_seconds=60)
    assert guards.try_acquire_ticket(42)

    (outcome,) = _run(fake, guards, JobHistory(), _job("d-1"))

    assert outcome.status == "skipped_in_flight"
    assert fake.calls == []
    assert not guards.try_acquire_ticket(42)
    assert guards.try_claim_delivery("d-1")


def test_ticket_is_released_after_a_failed_attempt() -> None:
    """A failing pipeline records the failure and releases per-ticket exclusion."""
    fake = _FailingZammad(tags=["pdf:sign"])
    guards = TicketGuards(delivery_id_ttl_seconds=60)
    history = JobHistory()

    (outcome,) = _run(fake, guards, history, _job())

    assert outcome.status == "failed_permanent"
    assert fake.calls == [("get_ticket",)]
    assert fake.tags == {"pdf:sign"}
    assert fake.notes == []
    assert history.read(1)[0]["status"] == "failed_permanent"
    assert guards.try_acquire_ticket(42)


def test_tag_fetch_failure_does_not_mutate_a_completed_ticket() -> None:
    """A transient eligibility read failure stays local until processing starts."""
    fake = _FailingTagFetchZammad(tags=["pdf:signed"])
    history = JobHistory()

    (outcome,) = _run(fake, TicketGuards(delivery_id_ttl_seconds=60), history, _job())

    assert outcome.status == "failed_transient"
    assert fake.calls == [("get_ticket",), ("list_tags",)]
    assert fake.tags == {"pdf:signed"}
    assert fake.notes == []
    assert history.read(1)[0]["status"] == "failed_transient"


def test_built_processor_reuses_caller_owned_client_without_closing() -> None:
    """Every scheduled job borrows the one application-owned client."""
    fake = _BorrowedZammad(tags=["other"])

    outcomes = _run(fake, TicketGuards(delivery_id_ttl_seconds=0), JobHistory(), _job(), _job())

    assert [outcome.status for outcome in outcomes] == ["skipped_not_triggered"] * 2
    assert fake.names == ["get_ticket", "list_tags", "get_ticket", "list_tags"]
