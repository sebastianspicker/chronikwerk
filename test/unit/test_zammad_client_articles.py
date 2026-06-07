from __future__ import annotations

import httpx
import respx

from test.support.checks import check
from test.support.zammad_client_helpers import run_client_action as _run_client_action
from zammad_pdf_archiver.adapters.zammad.client import AsyncZammadClient


def test_create_internal_article_success() -> None:
    async def assert_article(client: AsyncZammadClient) -> None:
        article = await client.create_internal_article(123, "Subject", "<p>Body</p>")
        check(not not article.id == 999, "assertion failed")
        check(not article.internal is not True, "assertion failed")
        check(not not article.subject == "Subject", "assertion failed")
        check(not not article.body == "<p>Body</p>", "assertion failed")

    with respx.mock:
        route = respx.post("https://zammad.example/api/v1/ticket_articles").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 999,
                    "internal": True,
                    "subject": "Subject",
                    "body": "<p>Body</p>",
                    "content_type": "text/html",
                    "created_at": "2024-01-02T00:00:00Z",
                },
            )
        )
        _run_client_action(assert_article)
        check(not not route.called, "assertion failed")


def test_get_attachment_content_success() -> None:
    """get_attachment_content returns raw bytes from ticket_attachment endpoint."""

    async def assert_attachment(client: AsyncZammadClient) -> None:
        data = await client.get_attachment_content(1, 2, 3)
        check(not not data == b"binary content", "assertion failed")

    with respx.mock:
        respx.get(
            "https://zammad.example/api/v1/ticket_attachment/1/2/3",
            headers={"Accept": "*/*"},
        ).mock(return_value=httpx.Response(200, content=b"binary content"))
        _run_client_action(assert_attachment)


def test_list_articles_success() -> None:
    async def assert_articles(client: AsyncZammadClient) -> None:
        articles = await client.list_articles(123)
        check(not not [a.id for a in articles] == [1, 2], "assertion failed")
        check(not not articles[0].from_ == "agent@example.com", "assertion failed")

    with respx.mock:
        respx.get("https://zammad.example/api/v1/ticket_articles/by_ticket/123").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "id": 1,
                        "created_at": "2024-01-01T00:00:00Z",
                        "internal": False,
                        "subject": "Hello",
                        "body": "Body",
                        "content_type": "text/plain",
                        "from": "agent@example.com",
                        "to": "support@example.com",
                        "attachments": [{"id": 10, "filename": "a.txt", "size": 123}],
                    },
                    {
                        "id": 2,
                        "created_at": "2024-01-02T00:00:00Z",
                        "internal": True,
                        "subject": "Note",
                        "body": "Internal",
                        "content_type": "text/plain",
                    },
                ],
            )
        )
        _run_client_action(assert_articles)
