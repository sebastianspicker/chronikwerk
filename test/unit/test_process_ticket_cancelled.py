from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import zammad_pdf_archiver.app.jobs.process_ticket as process_ticket_module
from test.support.checks import check
from test.support.settings_factory import make_settings
from zammad_pdf_archiver.adapters.zammad.models import TagList
from zammad_pdf_archiver.app.jobs import ticket_stores
from zammad_pdf_archiver.domain.state_machine import ERROR_TAG, PROCESSING_TAG, TRIGGER_TAG


class _FakeClient:
    added_tags: list[str] = []
    articles: list[tuple[int, str, str]] = []
    removed_tags: list[str] = []

    def __init__(self, **kwargs) -> None:  # noqa: ANN003, ARG002
        pass

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None

    async def get_ticket(self, ticket_id: int) -> SimpleNamespace:
        return SimpleNamespace(
            id=ticket_id,
            number="12345",
            title="cancelled",
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
        return TagList([TRIGGER_TAG])

    async def remove_tag(self, ticket_id: int, tag: str) -> None:  # noqa: ARG002
        type(self).removed_tags.append(tag)

    async def add_tag(self, ticket_id: int, tag: str) -> None:  # noqa: ARG002
        if tag == PROCESSING_TAG:
            raise asyncio.CancelledError()
        type(self).added_tags.append(tag)

    async def create_internal_article(self, ticket_id: int, subject: str, body: str) -> None:
        type(self).articles.append((ticket_id, subject, body))


def test_process_ticket_cancellation_during_tag_update_does_not_run_error_flow(
    monkeypatch,
    tmp_path,
) -> None:
    ticket_stores._reset_for_tests()
    _FakeClient.added_tags = []
    _FakeClient.articles = []
    _FakeClient.removed_tags = []
    settings = make_settings(str(tmp_path))

    monkeypatch.setattr(process_ticket_module, "AsyncZammadClient", _FakeClient)

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
