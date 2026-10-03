"""Verify classified archive failures preserve retry semantics and tag cleanup."""

from __future__ import annotations

import asyncio
import errno
from types import SimpleNamespace
from typing import cast

import pytest

from chronikwerk.archiving.options import ArchiveRuntimeOptions
from chronikwerk.archiving.workflow import ArchiveAttempt
from chronikwerk.archiving.workflow_errors import handle_ticket_pipeline_exception
from chronikwerk.operations.history import JobHistory
from chronikwerk.zammad.errors import ServerError
from tests.support.fake_zammad import FakeZammad


@pytest.mark.parametrize(
    ("exc", "expected_status", "keep_trigger"),
    [
        (OSError(errno.EAGAIN, "retry later"), "failed_transient", True),
        (ValueError("archive_path must not be empty"), "failed_permanent", False),
    ],
)
def test_failure_outcome_classifies_and_projects_retry_state(
    exc: Exception,
    expected_status: str,
    keep_trigger: bool,
) -> None:
    """Transient failures keep the trigger tag, permanent ones remove it, and a note is posted."""
    fake = FakeZammad(tags=["pdf:processing", "archive:ready"])
    fake.fail_note = True
    attempt = ArchiveAttempt(
        runtime=cast(ArchiveRuntimeOptions, SimpleNamespace()),
        ticket_id=42,
        delivery_id="delivery-42",
        request_id="request-42",
        history=JobHistory(),
    )

    outcome = asyncio.run(
        handle_ticket_pipeline_exception(
            client=fake.client,
            attempt=attempt,
            trigger_tag="archive:ready",
            exc=exc,
        )
    )

    assert outcome.status == expected_status
    assert outcome.ticket_id == 42
    assert outcome.classification == ("Transient" if keep_trigger else "Permanent")
    assert fake.tags == ({"archive:ready", "pdf:error"} if keep_trigger else {"pdf:error"})


def test_failure_note_contains_delivery_id() -> None:
    """The operator note references the delivery that failed."""
    fake = FakeZammad(tags=["archive:ready"])
    attempt = ArchiveAttempt(
        runtime=cast(ArchiveRuntimeOptions, SimpleNamespace()),
        ticket_id=42,
        delivery_id="delivery-42",
        request_id="request-42",
        history=JobHistory(),
    )

    asyncio.run(
        handle_ticket_pipeline_exception(
            client=fake.client,
            attempt=attempt,
            trigger_tag="archive:ready",
            exc=OSError(errno.EAGAIN, "retry later"),
        )
    )

    assert len(fake.notes) == 1
    assert "delivery-42" in fake.notes[0][1]
    assert "archive:ready" in fake.notes[0][1]


def test_dual_typed_server_error_note_follows_transient_label() -> None:
    """A retryable Zammad server error is labelled transient and never gets a permanent code."""
    fake = FakeZammad(tags=["archive:ready"])
    attempt = ArchiveAttempt(
        runtime=cast(ArchiveRuntimeOptions, SimpleNamespace()),
        ticket_id=42,
        delivery_id="delivery-42",
        request_id="request-42",
        history=JobHistory(),
    )

    outcome = asyncio.run(
        handle_ticket_pipeline_exception(
            client=fake.client,
            attempt=attempt,
            trigger_tag="archive:ready",
            exc=ServerError("upstream unavailable"),
        )
    )

    assert outcome.classification == "Transient"
    assert "Transient" in fake.notes[0][1]
    assert "permanent_error" not in fake.notes[0][1]
