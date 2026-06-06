from __future__ import annotations

import asyncio

import pytest

from test.support.checks import check
from zammad_pdf_archiver.adapters.snapshot.build_snapshot import enrich_attachment_content
from zammad_pdf_archiver.domain.snapshot_models import (
    Article,
    AttachmentMeta,
    Snapshot,
    TicketMeta,
)


class RecordingAttachmentClient:
    def __init__(self, content: bytes = b"x" * 9) -> None:
        self.calls: list[tuple[int, int, int]] = []
        self._content = content

    async def get_attachment_content(
        self, ticket_id: int, article_id: int, attachment_id: int
    ) -> bytes:
        self.calls.append((ticket_id, article_id, attachment_id))
        return self._content


def snapshot_with_attachment(
    attachment: AttachmentMeta,
    *,
    ticket_id: int = 1,
) -> Snapshot:
    return Snapshot(
        ticket=TicketMeta(id=ticket_id, number="T1", title="t"),
        articles=[
            Article(
                id=1,
                body_html="",
                body_text="",
                attachments=[attachment],
            )
        ],
    )


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

    snapshot = snapshot_with_attachment(
        AttachmentMeta(article_id=1, attachment_id=10, filename="a.txt", size=5)
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

    snapshot = snapshot_with_attachment(
        AttachmentMeta(article_id=1, attachment_id=10, filename="a.txt", size=11)
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
    client = RecordingAttachmentClient()

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
    client = RecordingAttachmentClient(b"small")

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
        not not large.content_omission_reason == "per_file_limit_declared_size",
        "assertion failed",
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
