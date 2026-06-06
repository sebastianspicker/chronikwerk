from __future__ import annotations

import asyncio

import pytest

import zammad_pdf_archiver.app.jobs.process_ticket as process_ticket_module
from test.support.process_ticket_cleanup_helpers import (
    ERROR_TAG,
    PROCESSING_TAG,
    _patch_process_ticket_client,
    _settings,
    _SimpleProcessTicketClient,
    check,
    ticket_stores,
)


class _FakeClient(_SimpleProcessTicketClient):
    removed_tags: list[str] = []
    ticket_title = "cancelled"

    async def remove_tag(self, ticket_id: int, tag: str) -> None:  # noqa: ARG002
        type(self).removed_tags.append(tag)

    async def add_tag(self, ticket_id: int, tag: str) -> None:  # noqa: ARG002
        if tag == PROCESSING_TAG:
            raise asyncio.CancelledError()
        type(self).added_tags.append(tag)

def test_process_ticket_cancellation_during_tag_update_does_not_run_error_flow(
    monkeypatch,
    tmp_path,
) -> None:
    ticket_stores._reset_for_tests()
    _FakeClient.added_tags = []
    _FakeClient.articles = []
    _FakeClient.removed_tags = []
    settings = _settings(tmp_path)

    _patch_process_ticket_client(monkeypatch, _FakeClient)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            process_ticket_module.process_ticket(
                "delivery-1",
                {"ticket_id": 1, "request_id": "req-1"},
                settings,
            )
        )

    check(not not _FakeClient.articles == [], "assertion failed")
    check(not not ERROR_TAG not in _FakeClient.added_tags, "assertion failed")
    check(not not PROCESSING_TAG not in _FakeClient.added_tags, "assertion failed")
