from __future__ import annotations

import asyncio
from typing import Any, cast

from test.support.checks import check
from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.jobs import _ticket_renderer
from zammad_pdf_archiver.domain.snapshot_models import (
    Article,
    AttachmentMeta,
    Snapshot,
    TicketMeta,
)


def test_build_and_render_pdf_reports_capped_articles_and_skipped_attachments(
    monkeypatch,
    tmp_path,
) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={
            "pdf": {
                "max_articles": 1,
                "article_limit_mode": "cap_and_continue",
            }
        },
    )

    async def _build_snapshot(*args: object, **kwargs: object) -> Snapshot:
        return Snapshot(
            ticket=TicketMeta(id=123, number="T123"),
            articles=[Article(id=1), Article(id=2)],
        )

    async def _enrich_attachment_content(
        snapshot: Snapshot,
        client: object,  # noqa: ARG001
        *,
        include_attachment_binary: bool,  # noqa: ARG001
        max_attachment_bytes_per_file: int,  # noqa: ARG001
        max_total_attachment_bytes: int,  # noqa: ARG001
    ) -> Snapshot:
        check(not not [article.id for article in snapshot.articles] == [1], "assertion failed")
        return Snapshot(
            ticket=snapshot.ticket,
            articles=[
                Article(
                    id=1,
                    attachments=[
                        AttachmentMeta(
                            article_id=1,
                            attachment_id=10,
                            content_omission_reason="total_budget_exhausted",
                        ),
                        AttachmentMeta(
                            article_id=1,
                            attachment_id=11,
                            content=b"kept",
                        ),
                    ],
                )
            ],
        )

    def _render_pdf(
        snapshot: Snapshot,
        template_variant: str,  # noqa: ARG001
        **kwargs: object,  # noqa: ARG001
    ) -> bytes:
        check(not not len(snapshot.articles) == 1, "assertion failed")
        return b"%PDF-1.7\n%%EOF\n"

    monkeypatch.setattr(_ticket_renderer, "build_snapshot", _build_snapshot)
    monkeypatch.setattr(_ticket_renderer, "enrich_attachment_content", _enrich_attachment_content)
    monkeypatch.setattr(_ticket_renderer, "render_pdf", _render_pdf)

    pdf_bytes, snapshot, articles_capped, attachments_skipped = asyncio.run(
        _ticket_renderer.build_and_render_pdf(
            cast(Any, object()),
            cast(Any, object()),
            cast(Any, object()),
            123,
            settings,
        )
    )

    check(not not pdf_bytes.startswith(b"%PDF"), "assertion failed")
    check(not not [article.id for article in snapshot.articles] == [1], "assertion failed")
    check(not articles_capped is not True, "assertion failed")
    check(not not attachments_skipped == 1, "assertion failed")
