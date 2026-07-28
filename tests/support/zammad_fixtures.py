"""Build representative Zammad API payloads shared by integration tests."""

from __future__ import annotations

import httpx
import respx


def archived_ticket_json(
    *,
    ticket_id: int = 123,
    archive_path: str | list[str] = "A > B > C",
    title: str = "Example Ticket",
) -> dict[str, object]:
    """Return a ticket response that exercises archive-path processing."""
    return {
        "id": ticket_id,
        "number": "20240123",
        "title": title,
        "owner": {"login": "agent"},
        "updated_by": {"login": "fallback-agent"},
        "preferences": {
            "custom_fields": {
                "archive_user_mode": "owner",
                "archive_path": archive_path,
            }
        },
    }


def html_article_json() -> dict[str, object]:
    """Return one representative external HTML ticket article."""
    return {
        "id": 1,
        "created_at": "2026-02-07T11:59:00Z",
        "internal": False,
        "subject": "Hello",
        "body": "<p>Hello World</p>",
        "content_type": "text/html",
        "from": "customer@example.invalid",
        "attachments": [],
    }


def register_archived_ticket_fetch_routes(*, articles: list[dict[str, object]]) -> None:
    """Stub the ticket, trigger-tag, and article reads used by archive scenarios."""
    respx.get("https://zammad.example.local/api/v1/tickets/123").mock(
        return_value=httpx.Response(200, json=archived_ticket_json())
    )
    respx.get(
        "https://zammad.example.local/api/v1/tags",
        params={"object": "Ticket", "o_id": "123"},
    ).mock(return_value=httpx.Response(200, json=["pdf:sign"]))
    respx.get("https://zammad.example.local/api/v1/ticket_articles/by_ticket/123").mock(
        return_value=httpx.Response(200, json=articles)
    )


def register_archive_mutation_routes() -> None:
    """Stub the archive tag transitions and internal-note write."""
    respx.post("https://zammad.example.local/api/v1/tags/remove").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    respx.post("https://zammad.example.local/api/v1/tags/add").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    respx.post("https://zammad.example.local/api/v1/ticket_articles").mock(
        return_value=httpx.Response(200, json={"id": 999})
    )
