from __future__ import annotations

import asyncio

import pytest

import zammad_pdf_archiver.adapters.snapshot.build_snapshot as build_snapshot_module
from test.support.checks import check
from zammad_pdf_archiver.adapters.snapshot.build_snapshot import (
    build_snapshot,
    enrich_attachment_content,
)
from zammad_pdf_archiver.adapters.zammad.models import (
    Article as ZammadArticle,
)
from zammad_pdf_archiver.adapters.zammad.models import (
    TagList,
)
from zammad_pdf_archiver.adapters.zammad.models import (
    Ticket as ZammadTicket,
)
from zammad_pdf_archiver.domain.snapshot_models import (
    Article,
    AttachmentMeta,
    Snapshot,
    TicketMeta,
)


class _FakeZammadClient:
    def __init__(
        self,
        *,
        ticket: ZammadTicket,
        tags: list[str],
        articles: list[ZammadArticle],
    ) -> None:
        self._ticket = ticket
        self._tags = tags
        self._articles = articles

    async def get_ticket(self, _: int) -> ZammadTicket:
        return self._ticket

    async def list_tags(self, _: int) -> TagList:
        return TagList(self._tags)

    async def list_articles(self, _: int) -> list[ZammadArticle]:
        return self._articles


class _CapturingLog:
    def __init__(self) -> None:
        self.warning_events: list[tuple[str, dict[str, object]]] = []

    def warning(self, event: str, **kwargs: object) -> None:
        self.warning_events.append((event, kwargs))


def test_articles_are_sorted_chronologically() -> None:
    async def run() -> None:
        ticket = ZammadTicket.model_validate({"id": 1, "number": "T1"})
        articles = [
            ZammadArticle.model_validate(
                {"id": 2, "created_at": "2024-01-02T00:00:00Z", "body": "later"}
            ),
            ZammadArticle.model_validate(
                {"id": 1, "created_at": "2024-01-01T00:00:00Z", "body": "earlier"}
            ),
        ]
        client = _FakeZammadClient(ticket=ticket, tags=[], articles=articles)

        snapshot = await build_snapshot(client, 1)
        check(not not [a.id for a in snapshot.articles] == [1, 2], "assertion failed")

    asyncio.run(run())


def test_strip_html_to_text_logs_warning_on_parse_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class _BrokenHTMLToText:
        def feed(self, html: str) -> None:  # noqa: ARG002
            raise ValueError("parse failed")

        def close(self) -> None:
            raise AssertionError("close should not run after feed failure")

        def get_text(self) -> str:
            return "unreachable"

    capture = _CapturingLog()
    monkeypatch.setattr(build_snapshot_module, "_HTMLToText", _BrokenHTMLToText)
    monkeypatch.setattr(build_snapshot_module, "log", capture)

    check(
        not not build_snapshot_module._strip_html_to_text("<p>broken</p>") == "", "assertion failed"
    )  # noqa: SLF001
    check(
        not not capture.warning_events == [("html_strip_failed", {"exc_info": True})],
        "assertion failed",
    )


def test_internal_flag_maps_none_to_false() -> None:
    async def run() -> None:
        ticket = ZammadTicket.model_validate({"id": 1, "number": "T1"})
        articles = [
            ZammadArticle.model_validate(
                {"id": 1, "created_at": "2024-01-01T00:00:00Z", "internal": None, "body": "x"}
            )
        ]
        client = _FakeZammadClient(ticket=ticket, tags=[], articles=articles)

        snapshot = await build_snapshot(client, 1)
        check(not snapshot.articles[0].internal is not False, "assertion failed")

    asyncio.run(run())


def test_html_is_stripped_to_text_and_falls_back_to_body() -> None:
    async def run() -> None:
        ticket = ZammadTicket.model_validate({"id": 1, "number": "T1"})
        articles = [
            ZammadArticle.model_validate(
                {
                    "id": 1,
                    "created_at": "2024-01-01T00:00:00Z",
                    "content_type": "text/html",
                    "body": "<p>Hello <b>World</b></p>",
                }
            ),
            ZammadArticle.model_validate(
                {
                    "id": 2,
                    "created_at": "2024-01-02T00:00:00Z",
                    "content_type": "text/html",
                    "body": "<p><br/></p>",
                }
            ),
        ]
        client = _FakeZammadClient(ticket=ticket, tags=[], articles=articles)

        snapshot = await build_snapshot(client, 1)
        check(not not snapshot.articles[0].body_text == "Hello World", "assertion failed")
        check(not not snapshot.articles[1].body_text == "<p><br/></p>", "assertion failed")

    asyncio.run(run())


def test_attachment_metadata_extraction_is_robust() -> None:
    async def run() -> None:
        ticket = ZammadTicket.model_validate({"id": 1, "number": "T1"})
        articles = [
            ZammadArticle.model_validate(
                {
                    "id": 1,
                    "created_at": "2024-01-01T00:00:00Z",
                    "attachments": [
                        {"id": 10, "filename": "a.txt", "size": 123, "content_type": "text/plain"},
                        {"filename": "missing-id.bin"},
                    ],
                    "body": "x",
                }
            )
        ]
        client = _FakeZammadClient(ticket=ticket, tags=[], articles=articles)

        snapshot = await build_snapshot(client, 1)
        check(not not len(snapshot.articles[0].attachments) == 2, "assertion failed")
        check(not not snapshot.articles[0].attachments[0].article_id == 1, "assertion failed")
        check(not not snapshot.articles[0].attachments[0].attachment_id == 10, "assertion failed")
        check(not snapshot.articles[0].attachments[1].attachment_id is not None, "assertion failed")
        check(
            not not snapshot.articles[0].attachments[1].filename == "missing-id.bin",
            "assertion failed",
        )

    asyncio.run(run())


def test_body_html_is_sanitized_for_safe_pdf_rendering() -> None:
    async def run() -> None:
        ticket = ZammadTicket.model_validate({"id": 1, "number": "T1"})
        articles = [
            ZammadArticle.model_validate(
                {
                    "id": 1,
                    "created_at": "2024-01-01T00:00:00Z",
                    "content_type": "text/html",
                    "body": (
                        '<p onclick="x">Hello '
                        "<script>alert(1)</script>"
                        '<a href="javascript:alert(1)">bad</a> '
                        '<a href="https://example.com">ok</a>'
                        "</p>"
                    ),
                }
            )
        ]
        client = _FakeZammadClient(ticket=ticket, tags=[], articles=articles)

        snapshot = await build_snapshot(client, 1)
        body_html = snapshot.articles[0].body_html
        check(not not "<script" not in body_html, "assertion failed")
        check(not not "onclick" not in body_html, "assertion failed")
        check(not not "javascript:" not in body_html, "assertion failed")
        check(not 'href="https://example.com"' not in body_html, "assertion failed")

    asyncio.run(run())


def test_plain_text_with_angle_brackets_is_not_treated_as_html() -> None:
    async def run() -> None:
        ticket = ZammadTicket.model_validate({"id": 1, "number": "T1"})
        articles = [
            ZammadArticle.model_validate(
                {
                    "id": 1,
                    "created_at": "2024-01-01T00:00:00Z",
                    "content_type": "text/plain",
                    "body": "Please include <foo> in the config.",
                }
            )
        ]
        client = _FakeZammadClient(ticket=ticket, tags=[], articles=articles)

        snapshot = await build_snapshot(client, 1)
        check(not not snapshot.articles[0].body_html == "", "assertion failed")
        check(
            not not snapshot.articles[0].body_text == "Please include <foo> in the config.",
            "assertion failed",
        )

    asyncio.run(run())


def test_enrich_attachment_content_records_omission_when_disabled() -> None:
    """Disabled binary inclusion must leave sidecar evidence for metadata-only attachments."""

    class FakeAttachmentClient:
        def __init__(self) -> None:
            self.calls = 0

        async def get_attachment_content(
            self, ticket_id: int, article_id: int, attachment_id: int
        ) -> bytes:
            self.calls += 1
            return b"should-not-fetch"

    snapshot = Snapshot(
        ticket=TicketMeta(id=1, number="T1", title="t"),
        articles=[
            Article(
                id=1,
                body_html="",
                body_text="",
                attachments=[
                    AttachmentMeta(article_id=1, attachment_id=10, filename="a.txt", size=5),
                ],
            )
        ],
    )
    client = FakeAttachmentClient()
    result = asyncio.run(
        enrich_attachment_content(
            snapshot,
            client,
            include_attachment_binary=False,
            max_attachment_bytes_per_file=1000,
            max_total_attachment_bytes=5000,
        )
    )
    attachment = result.articles[0].attachments[0]
    check(not not client.calls == 0, "assertion failed")
    check(not attachment.content is not None, "assertion failed")
    check(
        not not attachment.content_omission_reason == "binary_inclusion_disabled",
        "assertion failed",
    )


async def _run_enrich_fills_content() -> None:
    class FakeAttachmentClient:
        async def get_attachment_content(
            self, ticket_id: int, article_id: int, attachment_id: int
        ) -> bytes:
            return b"binary data"

    snapshot = Snapshot(
        ticket=TicketMeta(id=1, number="T1", title="t"),
        articles=[
            Article(
                id=1,
                body_html="",
                body_text="",
                attachments=[
                    AttachmentMeta(article_id=1, attachment_id=10, filename="a.txt", size=11),
                ],
            )
        ],
    )
    result = await enrich_attachment_content(
        snapshot,
        FakeAttachmentClient(),
        include_attachment_binary=True,
        max_attachment_bytes_per_file=100,
        max_total_attachment_bytes=1000,
    )
    attachment = result.articles[0].attachments[0]
    check(not not attachment.content == b"binary data", "assertion failed")
    check(not attachment.content_omission_reason is not None, "assertion failed")


def test_enrich_attachment_content_fills_content_when_enabled() -> None:
    """When include_attachment_binary is True and within limits, content is set."""
    asyncio.run(_run_enrich_fills_content())


class _BudgetAttachmentClient:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int, int]] = []

    async def get_attachment_content(
        self, ticket_id: int, article_id: int, attachment_id: int
    ) -> bytes:
        self.calls.append((ticket_id, article_id, attachment_id))
        return b"x" * 9


def _attachment_ids_with_content(snapshot: Snapshot) -> set[int | None]:
    return {
        att.attachment_id
        for article in snapshot.articles
        for att in article.attachments
        if att.content
    }


def _attachment_omission_reasons_without_content(
    snapshot: Snapshot,
) -> dict[int | None, str | None]:
    return {
        att.attachment_id: att.content_omission_reason
        for article in snapshot.articles
        for att in article.attachments
        if not att.content
    }


def test_enrich_attachment_content_stops_fetching_after_total_budget() -> None:
    """The total attachment budget must bound downloads, not only retained content."""

    attachments = [
        AttachmentMeta(article_id=1, attachment_id=i, filename=f"{i}.bin", size=9)
        for i in range(1, 6)
    ]
    snapshot = Snapshot(
        ticket=TicketMeta(id=123, number="T1", title="t"),
        articles=[Article(id=1, attachments=attachments)],
    )
    client = _BudgetAttachmentClient()

    result = asyncio.run(
        enrich_attachment_content(
            snapshot,
            client,
            include_attachment_binary=True,
            max_attachment_bytes_per_file=10,
            max_total_attachment_bytes=10,
        )
    )

    kept_ids = _attachment_ids_with_content(result)
    omission_reasons = _attachment_omission_reasons_without_content(result)
    check(not not len(client.calls) == 1, "assertion failed")
    check(not not kept_ids == {1}, "assertion failed")
    check(not not set(omission_reasons) == {2, 3, 4, 5}, "assertion failed")
    check(not not set(omission_reasons.values()) == {"total_budget_exhausted"}, "assertion failed")


def test_enrich_attachment_content_records_declared_size_skip() -> None:
    """Declared over-limit attachments are policy skips, not silent omissions."""

    class FakeAttachmentClient:
        def __init__(self) -> None:
            self.calls: list[tuple[int, int, int]] = []

        async def get_attachment_content(
            self, ticket_id: int, article_id: int, attachment_id: int
        ) -> bytes:
            self.calls.append((ticket_id, article_id, attachment_id))
            return b"small"

    snapshot = Snapshot(
        ticket=TicketMeta(id=123, number="T1", title="t"),
        articles=[
            Article(
                id=1,
                attachments=[
                    AttachmentMeta(article_id=1, attachment_id=1, filename="large.bin", size=11),
                    AttachmentMeta(article_id=1, attachment_id=2, filename="small.bin", size=5),
                ],
            )
        ],
    )
    client = FakeAttachmentClient()

    result = asyncio.run(
        enrich_attachment_content(
            snapshot,
            client,
            include_attachment_binary=True,
            max_attachment_bytes_per_file=10,
            max_total_attachment_bytes=100,
        )
    )

    large, small = result.articles[0].attachments
    check(not not client.calls == [(123, 1, 2)], "assertion failed")
    check(not large.content is not None, "assertion failed")
    check(
        not not large.content_omission_reason == "per_file_limit_declared_size", "assertion failed"
    )
    check(not not small.content == b"small", "assertion failed")
    check(not small.content_omission_reason is not None, "assertion failed")


def test_enrich_attachment_content_records_fetched_size_skip() -> None:
    """Fetched over-limit attachments are counted as skipped instead of disappearing."""

    class FakeAttachmentClient:
        async def get_attachment_content(
            self, ticket_id: int, article_id: int, attachment_id: int
        ) -> bytes:
            return b"x" * 11

    snapshot = Snapshot(
        ticket=TicketMeta(id=123, number="T1", title="t"),
        articles=[
            Article(
                id=1,
                attachments=[
                    AttachmentMeta(article_id=1, attachment_id=1, filename="grows.bin", size=5),
                ],
            )
        ],
    )

    result = asyncio.run(
        enrich_attachment_content(
            snapshot,
            FakeAttachmentClient(),
            include_attachment_binary=True,
            max_attachment_bytes_per_file=10,
            max_total_attachment_bytes=100,
        )
    )

    attachment = result.articles[0].attachments[0]
    check(not attachment.content is not None, "assertion failed")
    check(
        not not attachment.content_omission_reason == "per_file_limit_fetched_size",
        "assertion failed",
    )


def test_enrich_attachment_content_raises_when_enabled_fetch_fails() -> None:
    """Enabled binary archival must not silently omit fetchable attachments."""

    class FakeAttachmentClient:
        async def get_attachment_content(
            self, ticket_id: int, article_id: int, attachment_id: int
        ) -> bytes:
            raise RuntimeError("zammad attachment unavailable")

    snapshot = Snapshot(
        ticket=TicketMeta(id=123, number="T1", title="t"),
        articles=[
            Article(
                id=1,
                attachments=[
                    AttachmentMeta(article_id=1, attachment_id=10, filename="a.txt", size=5),
                ],
            )
        ],
    )

    with pytest.raises(RuntimeError, match="zammad attachment unavailable"):
        asyncio.run(
            enrich_attachment_content(
                snapshot,
                FakeAttachmentClient(),
                include_attachment_binary=True,
                max_attachment_bytes_per_file=100,
                max_total_attachment_bytes=1000,
            )
        )
