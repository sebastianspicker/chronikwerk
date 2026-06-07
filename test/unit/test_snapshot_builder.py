from __future__ import annotations

import asyncio

import pytest

import zammad_pdf_archiver.adapters.snapshot.build_snapshot as build_snapshot_module
from test.support.checks import check
from test.support.logging_helpers import CapturingWarningLog as _CapturingLog
from zammad_pdf_archiver.adapters.snapshot.build_snapshot import build_snapshot
from zammad_pdf_archiver.adapters.zammad.models import (
    Article as ZammadArticle,
)
from zammad_pdf_archiver.adapters.zammad.models import (
    TagList,
)
from zammad_pdf_archiver.adapters.zammad.models import (
    Ticket as ZammadTicket,
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


def _ticket(ticket_id: int = 1, number: str = "T1") -> ZammadTicket:
    return ZammadTicket.model_validate({"id": ticket_id, "number": number})


def _zammad_article(**overrides: object) -> ZammadArticle:
    data = {"id": 1, "created_at": "2024-01-01T00:00:00Z"}
    data.update(overrides)
    return ZammadArticle.model_validate(data)


def test_articles_are_sorted_chronologically() -> None:
    async def run() -> None:
        ticket = _ticket()
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
        ticket = _ticket()
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
        ticket = _ticket()
        articles = [
            _zammad_article(
                content_type="text/html",
                body="<p>Hello <b>World</b></p>",
            ),
            _zammad_article(
                id=2,
                created_at="2024-01-02T00:00:00Z",
                content_type="text/html",
                body="<p><br/></p>",
            ),
        ]
        client = _FakeZammadClient(ticket=ticket, tags=[], articles=articles)

        snapshot = await build_snapshot(client, 1)
        check(not not snapshot.articles[0].body_text == "Hello World", "assertion failed")
        check(not not snapshot.articles[1].body_text == "<p><br/></p>", "assertion failed")

    asyncio.run(run())


def test_attachment_metadata_extraction_is_robust() -> None:
    async def run() -> None:
        ticket = _ticket()
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
        ticket = _ticket()
        articles = [
            _zammad_article(
                content_type="text/html",
                body=(
                    '<p onclick="x">Hello '
                    "<script>alert(1)</script>"
                    '<a href="javascript:alert(1)">bad</a> '
                    '<a href="https://example.com">ok</a>'
                    "</p>"
                ),
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
        ticket = _ticket()
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
