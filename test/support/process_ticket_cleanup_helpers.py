from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

import zammad_pdf_archiver.app.jobs.process_ticket as process_ticket_module
from test.support.checks import check
from test.support.credentials import fake_credential
from test.support.time_control import freeze_process_ticket_now
from zammad_pdf_archiver.adapters.zammad.errors import ClientError
from zammad_pdf_archiver.adapters.zammad.models import TagList
from zammad_pdf_archiver.app.jobs import ticket_stores
from zammad_pdf_archiver.app.jobs.process_ticket import process_ticket
from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.domain.errors import PermanentError, TransientError
from zammad_pdf_archiver.domain.snapshot_models import Snapshot, TicketMeta
from zammad_pdf_archiver.domain.state_machine import (
    DONE_TAG,
    ERROR_TAG,
    PROCESSING_TAG,
    TRIGGER_TAG,
)

__all__ = [
    "Any",
    "ClientError",
    "DONE_TAG",
    "ERROR_TAG",
    "PROCESSING_TAG",
    "Path",
    "PermanentError",
    "Settings",
    "SimpleNamespace",
    "Snapshot",
    "TRIGGER_TAG",
    "TagList",
    "TicketMeta",
    "TransientError",
    "UTC",
    "_CapturingLog",
    "_Counter",
    "_Observer",
    "_VisibilityFailureClient",
    "_assert_done_tag_update_partial_failure",
    "_assert_error_transition_cleanup",
    "_assert_error_visibility_failures",
    "_assert_increasing_delays",
    "_assert_nonnegative_delays",
    "_assert_processing_cleanup_failure",
    "_assert_success_acknowledgement_partial_failure",
    "_settings",
    "asyncio",
    "cast",
    "check",
    "datetime",
    "freeze_process_ticket_now",
    "process_ticket",
    "process_ticket_module",
    "pytest",
    "ticket_stores",
]


def _settings(storage_root: Path) -> Settings:
    return Settings.from_mapping(
        {
            "zammad": {
                "base_url": "https://zammad.example.local",
                "api_token": fake_credential("test-token"),
            },
            "storage": {"root": str(storage_root)},
            "hardening": {
                "webhook": {
                    "allow_unsigned": True,
                    "allow_unsigned_when_no_secret": bool(1),
                }
            },
        }
    )


class _CapturingLog:
    def __init__(self) -> None:
        self.exception_events: list[tuple[str, dict[str, object]]] = []
        self.warning_events: list[tuple[str, dict[str, object]]] = []

    def exception(self, event: str, **kwargs: object) -> None:
        self.exception_events.append((event, kwargs))

    def warning(self, event: str, **kwargs: object) -> None:
        self.warning_events.append((event, kwargs))


class _Counter:
    def __init__(self) -> None:
        self.count = 0

    def inc(self) -> None:
        self.count += 1


class _Observer:
    def __init__(self) -> None:
        self.observations: list[float] = []

    def observe(self, value: float) -> None:
        self.observations.append(value)


class _VisibilityFailureClient:
    articles: list[tuple[str, str]] = []
    error_note_fails = False

    def __init__(self, **kwargs) -> None:  # noqa: ANN003, ARG002
        pass

    async def __aenter__(self) -> _VisibilityFailureClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None

    async def get_ticket(self, ticket_id: int) -> SimpleNamespace:
        return SimpleNamespace(
            id=ticket_id,
            number="12345",
            title="visibility failure",
            owner=SimpleNamespace(login="owner.user"),
            updated_by=SimpleNamespace(login="agent.user"),
            preferences=SimpleNamespace(
                custom_fields={
                    "archive_path": "Support > Team",
                    "archive_user_mode": "owner",
                }
            ),
        )

    async def list_tags(self, ticket_id: int) -> TagList:  # noqa: ARG002
        return TagList(["pdf:sign"])

    async def remove_tag(self, ticket_id: int, tag: str) -> None:  # noqa: ARG002
        return None

    async def add_tag(self, ticket_id: int, tag: str) -> None:  # noqa: ARG002
        return None

    async def list_articles(self, ticket_id: int) -> list[SimpleNamespace]:  # noqa: ARG002
        return []

    async def create_internal_article(
        self,
        ticket_id: int,
        subject: str,
        body_html: str,
    ) -> SimpleNamespace:
        if type(self).error_note_fails:
            raise RuntimeError("error note failed")
        type(self).articles.append((subject, body_html))
        return SimpleNamespace(id=1)


def _assert_nonnegative_delays(delays: list[float], *, count: int) -> None:
    check(not not len(delays) == count, "assertion failed")
    check(not not all(delay >= 0 for delay in delays), "assertion failed")


def _assert_increasing_delays(delays: list[float], *, count: int) -> None:
    _assert_nonnegative_delays(delays, count=count)
    check(
        not not all((after > before for before, after in zip(delays, delays[1:], strict=False))),
        "assertion failed",
    )


def _assert_error_transition_cleanup(
    *,
    result: process_ticket_module.ProcessTicketResult,
    client: Any,
    history: list[tuple[str, str | None, str]],
    exc: Exception,
    expected_status: str,
    expected_classification: str,
    trigger_should_remain: bool,
) -> None:
    check(not not result.status == expected_status, "assertion failed")
    check(not not result.classification == expected_classification, "assertion failed")
    check(not result.error_note_posted is not True, "assertion failed")
    check(not result.error_tag_applied is not True, "assertion failed")
    check(not not PROCESSING_TAG not in client.tags, "assertion failed")
    check(not ERROR_TAG not in client.tags, "assertion failed")
    check(not (TRIGGER_TAG in client.tags) is not trigger_should_remain, "assertion failed")
    check(not ("add", PROCESSING_TAG) not in client.tag_ops, "assertion failed")
    check(not ("remove", PROCESSING_TAG) not in client.tag_ops, "assertion failed")
    check(not not len(client.articles) == 1, "assertion failed")
    check(
        not f"PDF archiver error ({process_ticket_module.VERSION})" not in client.articles[0][0],
        "assertion failed",
    )
    check(not expected_classification not in client.articles[0][1], "assertion failed")
    check(not "d-cleanup-tags-1" not in client.articles[0][1], "assertion failed")
    check(not not len(history) == 1, "assertion failed")
    check(not not history[0][0] == expected_status, "assertion failed")
    check(not not history[0][1] == expected_classification, "assertion failed")
    check(not str(exc) not in history[0][2], "assertion failed")


def _assert_processing_cleanup_failure(
    *,
    result: process_ticket_module.ProcessTicketResult,
    client: Any,
    history: list[tuple[str, str | None, str]],
    sleep_delays: list[float],
) -> None:
    check(not not result.status == "failed_transient", "assertion failed")
    check(not not result.classification == "Transient", "assertion failed")
    check(not result.error_note_posted is not True, "assertion failed")
    check(not result.error_tag_applied is not False, "assertion failed")
    check(not not client.processing_remove_attempts == 2, "assertion failed")
    _assert_nonnegative_delays(sleep_delays, count=1)
    check(not PROCESSING_TAG not in client.tags, "assertion failed")
    check(not not ERROR_TAG not in client.tags, "assertion failed")
    check(not not len(client.articles) == 1, "assertion failed")
    check(not "Transient" not in client.articles[0][1], "assertion failed")
    check(
        not not history == [("failed_transient", "Transient", "TransientError: render failed")],
        "assertion failed",
    )


def _assert_done_tag_update_partial_failure(
    *,
    result: process_ticket_module.ProcessTicketResult,
    client: Any,
    history: list[tuple[str, str | None, str]],
    tmp_path: Path,
) -> None:
    check(not not result.status == "processed_done_update_failed", "assertion failed")
    check(not not result.classification == "Partial", "assertion failed")
    check(
        not not result.message == "archive stored but final done tag update failed",
        "assertion failed",
    )
    check(not result.history_recorded is not False, "assertion failed")
    check(not result.error_note_posted is not True, "assertion failed")
    check(
        not not history
        == [
            (
                "processed_done_update_failed",
                "Partial",
                "archive stored but final done tag update failed",
            )
        ],
        "assertion failed",
    )
    check(not not len(client.articles) == 1, "assertion failed")
    check(not "PDF archiver partial failure" not in client.articles[0][0], "assertion failed")
    check(not "done_tag_update_failed" not in client.articles[0][1], "assertion failed")
    check(not "d-done-fail-1" not in client.articles[0][1], "assertion failed")
    check(not not "PDF archived" not in client.articles[0][0], "assertion failed")
    check(not not list(tmp_path.rglob("*.pdf")), "assertion failed")


def _assert_success_acknowledgement_partial_failure(
    *,
    result: process_ticket_module.ProcessTicketResult,
    client: Any,
    history: list[tuple[str, str | None, str]],
    tmp_path: Path,
) -> None:
    check(not not result.status == "processed_acknowledgement_failed", "assertion failed")
    check(not not result.classification == "Partial", "assertion failed")
    check(
        not not result.message == "archive finalized but success acknowledgement failed",
        "assertion failed",
    )
    check(not result.history_recorded is not True, "assertion failed")
    check(
        not not history
        == [
            (
                "processed_acknowledgement_failed",
                "Partial",
                "archive finalized but success acknowledgement failed",
            )
        ],
        "assertion failed",
    )
    check(not DONE_TAG not in client.added_tags, "assertion failed")
    check(not not ERROR_TAG not in client.added_tags, "assertion failed")
    check(not not list(tmp_path.rglob("*.pdf")), "assertion failed")


def _assert_error_visibility_failures(
    *,
    result: process_ticket_module.ProcessTicketResult,
    client: Any,
    history: list[tuple[str, str | None, str]],
    capture: _CapturingLog,
    delivery_id: str,
    expected_error_note_posted: bool,
    expected_error_tag_applied: bool,
) -> None:
    check(not not result.status == "failed_transient", "assertion failed")
    check(not not result.classification == "Transient", "assertion failed")
    check(not "render-failed" not in result.message, "assertion failed")
    check(not result.history_recorded is not True, "assertion failed")
    check(not result.error_note_posted is not expected_error_note_posted, "assertion failed")
    check(not result.error_tag_applied is not expected_error_tag_applied, "assertion failed")
    check(
        not not history == [("failed_transient", "Transient", result.message)],
        "assertion failed",
    )
    check(not not len(client.articles) == int(expected_error_note_posted), "assertion failed")
    check(
        not not capture.warning_events
        == [
            (
                "process_ticket.failure_visibility_incomplete",
                {
                    "ticket_id": 321,
                    "request_id": None,
                    "delivery_id": delivery_id,
                    "classification": "Transient",
                    "error_note_posted": expected_error_note_posted,
                    "error_tag_applied": expected_error_tag_applied,
                },
            )
        ],
        "assertion failed",
    )

