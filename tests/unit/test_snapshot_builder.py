"""Verifies snapshots sort articles, sanitize HTML, and retain attachment metadata."""

from __future__ import annotations

import asyncio
from typing import Any, cast

from chronikwerk.adapters.snapshot.build_snapshot import build_snapshot
from chronikwerk.adapters.zammad.models import Article as ZammadArticle
from chronikwerk.adapters.zammad.models import TagList
from chronikwerk.adapters.zammad.models import Ticket as ZammadTicket


class _FakeZammadClient:
    """Async Zammad double that returns controlled ticket, tag, and article data."""

    def __init__(
        self,
        *,
        ticket: ZammadTicket,
        tags: list[str],
        articles: list[ZammadArticle],
    ) -> None:
        self._ticket = ticket
        self._tags = TagList.model_validate(tags)
        self._articles = articles

    async def get_ticket(self, ticket_id: int) -> ZammadTicket:
        return self._ticket

    async def list_tags(self, ticket_id: int) -> TagList:
        return self._tags

    async def list_articles(self, ticket_id: int) -> list[ZammadArticle]:
        return self._articles


async def _build_test_snapshot(*, tags: list[str], articles: list[ZammadArticle]) -> Any:
    """Build a snapshot with the common ticket/client setup."""
    ticket = ZammadTicket.model_validate({"id": 1, "number": "T1"})
    return await build_snapshot(
        cast(Any, _FakeZammadClient(ticket=ticket, tags=tags, articles=articles)),
        1,
    )


def test_articles_are_sorted_and_rich_html_is_sanitized() -> None:
    async def run() -> None:
        articles = [
            ZammadArticle.model_validate(
                {"id": 2, "created_at": "2024-01-02T00:00:00Z", "body": "later"}
            ),
            ZammadArticle.model_validate(
                {
                    "id": 1,
                    "created_at": "2024-01-01T00:00:00Z",
                    "body": "<b>earlier</b>",
                }
            ),
        ]
        snapshot = await _build_test_snapshot(tags=["pdf:sign"], articles=articles)
        assert [article.id for article in snapshot.articles] == [1, 2]
        assert snapshot.articles[0].body_html == "<b>earlier</b>"
        assert snapshot.articles[0].body_text == "earlier"

    asyncio.run(run())


def test_attachment_metadata_is_kept_without_binary_content() -> None:
    async def run() -> None:
        articles = [
            ZammadArticle.model_validate(
                {
                    "id": 1,
                    "attachments": [
                        {"id": 10, "filename": "a.txt", "size": 123},
                    ],
                    "body": "x",
                }
            )
        ]
        snapshot = await _build_test_snapshot(tags=[], articles=articles)
        attachment = snapshot.articles[0].attachments[0]
        assert attachment.article_id == 1
        assert attachment.attachment_id == 10
        assert attachment.filename == "a.txt"

    asyncio.run(run())
