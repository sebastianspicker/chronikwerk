from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import respx

from test.support.credentials import fake_credential
from zammad_pdf_archiver._version import VERSION
from zammad_pdf_archiver.adapters.storage.layout import build_filename_from_pattern
from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.domain.state_machine import (
    DONE_TAG,
    ERROR_TAG,
    PROCESSING_TAG,
    TRIGGER_TAG,
)


def zammad_storage_settings(
    storage_root: str,
    *,
    storage_overrides: dict[str, Any] | None = None,
    workflow: dict[str, Any] | None = None,
) -> Settings:
    storage: dict[str, Any] = {"root": storage_root}
    if storage_overrides is not None:
        storage.update(storage_overrides)

    values: dict[str, Any] = {
        "zammad": {
            "base_url": "https://zammad.example.local",
            "api_token": fake_credential("test-token"),
        },
        "storage": storage,
    }
    if workflow is not None:
        values["workflow"] = workflow

    return Settings.from_mapping(values)


def expected_agent_archive_pdf_path(
    tmp_path: Path,
    *,
    settings: Settings,
    fixed_now: datetime,
    ticket_number: str = "20240123",
) -> Path:
    filename = build_filename_from_pattern(
        settings.storage.path_policy.filename_pattern,
        ticket_number=ticket_number,
        timestamp_utc=fixed_now.date().isoformat(),
    )
    return tmp_path / "agent" / "A" / "B" / "C" / filename


def called_tag_items(route: respx.Route) -> list[str]:
    items: list[str] = []
    for call in route.calls:
        body = json.loads(call.request.content.decode("utf-8"))
        items.append(body.get("item"))
    return items


def assert_success_tag_transitions(
    *,
    add_tag_route: respx.Route,
    remove_tag_route: respx.Route,
) -> None:
    from test.support.checks import check

    added = called_tag_items(add_tag_route)
    removed = called_tag_items(remove_tag_route)

    check(not PROCESSING_TAG not in added, "assertion failed")
    check(not DONE_TAG not in added, "assertion failed")
    check(not not ERROR_TAG not in added, "assertion failed")
    check(not TRIGGER_TAG not in removed, "assertion failed")
    check(not ERROR_TAG not in removed, "assertion failed")
    check(not PROCESSING_TAG not in removed, "assertion failed")


def posted_article(route: respx.Route) -> dict[str, Any]:
    from test.support.checks import check

    check(not not route.called, "assertion failed")
    return json.loads(route.calls[0].request.content.decode("utf-8"))


def assert_error_article_note(
    route: respx.Route,
    *,
    classification: str,
    body_texts: tuple[str, ...] = (),
) -> dict[str, Any]:
    from test.support.checks import check

    article = posted_article(route)
    check(not f"PDF archiver error ({VERSION})" not in str(article["subject"]), "assertion failed")
    body = str(article["body"])
    check(not classification not in body, "assertion failed")
    for text in body_texts:
        check(not text not in body, "assertion failed")
    return article


def zammad_ticket_payload(
    *,
    ticket_id: int = 123,
    title: str | None = None,
    archive_path: object = "A > B > C",
) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": ticket_id,
        "number": "20240123",
        "owner": {"login": "agent"},
        "updated_by": {"login": "fallback-agent"},
        "preferences": {
            "custom_fields": {
                "archive_user_mode": "owner",
                "archive_path": archive_path,
            }
        },
    }
    if title is not None:
        payload["title"] = title
    return payload


def zammad_article_payload(
    *,
    attachments: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "id": 1,
        "created_at": "2026-02-07T11:59:00Z",
        "internal": False,
        "subject": "Hello",
        "body": "<p>Hello World</p>",
        "content_type": "text/html",
        "from": "customer@example.invalid",
        "attachments": [] if attachments is None else attachments,
    }


def mock_standard_zammad_reads(
    *,
    ticket_payload: dict[str, object] | None = None,
    tags: list[str] | None = None,
    articles: list[dict[str, object]] | None = None,
) -> None:
    respx.get("https://zammad.example.local/api/v1/tickets/123").mock(
        return_value=httpx.Response(
            200,
            json=ticket_payload
            or zammad_ticket_payload(title="Example Ticket", archive_path="A > B > C"),
        )
    )
    respx.get(
        "https://zammad.example.local/api/v1/tags",
        params={"object": "Ticket", "o_id": "123"},
    ).mock(return_value=httpx.Response(200, json=tags or [TRIGGER_TAG]))
    respx.get("https://zammad.example.local/api/v1/ticket_articles/by_ticket/123").mock(
        return_value=httpx.Response(
            200,
            json=[zammad_article_payload()] if articles is None else articles,
        )
    )


def mock_success_tag_write_routes() -> tuple[respx.Route, respx.Route]:
    remove_tag_route = respx.post("https://zammad.example.local/api/v1/tags/remove").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    add_tag_route = respx.post("https://zammad.example.local/api/v1/tags/add").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    return remove_tag_route, add_tag_route


def mock_success_zammad_write_routes(
    *,
    article_response: dict[str, Any] | None = None,
) -> respx.Route:
    mock_success_tag_write_routes()
    return respx.post("https://zammad.example.local/api/v1/ticket_articles").mock(
        return_value=httpx.Response(200, json=article_response or {"id": 999})
    )


def assert_disabled_history_response(response: Any) -> None:
    assert_json_response(
        response,
        {"status": "disabled", "available": False, "count": 0, "truncated": False, "items": []},
    )


def assert_json_response(response: Any, expected: dict[str, Any]) -> None:
    check_status_ok(response)
    from test.support.checks import check

    check(not not response.json() == expected, "assertion failed")


def check_status_ok(response: Any) -> None:
    from test.support.checks import check

    check(not not response.status_code == 200, "assertion failed")
