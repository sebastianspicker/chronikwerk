"""Verifies workflow tag, acknowledgement, and processing settings take effect."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import httpx
import respx

from chronikwerk.adapters.storage.layout import build_filename_from_pattern
from chronikwerk.app.jobs import _ticket_pipeline as ticket_pipeline_module
from chronikwerk.app.jobs.process_ticket import process_ticket
from chronikwerk.config.settings import Settings
from chronikwerk.domain.state_machine import DONE_TAG, ERROR_TAG, PROCESSING_TAG
from tests.support.settings_factory import make_settings


def _settings(storage_root: str, *, workflow: dict | None = None) -> Settings:
    """Build settings isolated to this test scenario."""
    return make_settings(
        storage_root,
        overrides={"workflow": workflow or {}},
    )


def _called_tag_items(route: respx.Route) -> list[str]:
    """Return a collector for Zammad tag mutation calls."""
    items: list[str] = []
    for call in route.calls:
        body = json.loads(call.request.content.decode("utf-8"))
        items.append(body.get("item"))
    return items


def _mock_ticket(*, ticket_id: int = 123) -> None:
    """Stub a ticket whose fields select owner-based archive routing."""
    respx.get(f"https://zammad.example.local/api/v1/tickets/{ticket_id}").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": ticket_id,
                "number": "20240123",
                "owner": {"login": "agent"},
                "updated_by": {"login": "fallback-agent"},
                "preferences": {
                    "custom_fields": {
                        "archive_user_mode": "owner",
                        "archive_path": "A > B > C",
                    }
                },
            },
        )
    )


def _mock_articles(*, ticket_id: int = 123) -> None:
    """Stub the minimal article payload needed to render an archive."""
    respx.get(f"https://zammad.example.local/api/v1/ticket_articles/by_ticket/{ticket_id}").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": 1,
                    "created_at": "2026-02-07T11:59:00Z",
                    "internal": False,
                    "subject": "Hello",
                    "body": "<p>Hello World</p>",
                    "content_type": "text/html",
                    "from": "customer@example.invalid",
                    "attachments": [],
                }
            ],
        )
    )


def _mock_tag_routes() -> tuple[respx.Route, respx.Route]:
    """Stub tag mutations and retain routes for workflow assertions."""
    remove_tag_route = respx.post("https://zammad.example.local/api/v1/tags/remove").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    add_tag_route = respx.post("https://zammad.example.local/api/v1/tags/add").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    return remove_tag_route, add_tag_route


def _assert_archive_exists(tmp_path, settings: Settings, fixed_now: datetime) -> None:
    """Assert the workflow produced the configured archive path."""
    expected_filename = build_filename_from_pattern(
        settings.storage.filename_pattern,
        ticket_number="20240123",
        timestamp_utc=fixed_now.date().isoformat(),
    )
    assert (tmp_path / "agent" / "A" / "B" / "C" / expected_filename).exists()


def _assert_success_tags(removed: list[str], added: list[str], *, trigger_tag: str) -> None:
    """Assert the common successful workflow tag transition."""
    assert trigger_tag in removed
    assert PROCESSING_TAG in added
    assert DONE_TAG in added
    assert ERROR_TAG not in added


def _run_workflow(
    tmp_path,
    monkeypatch,
    *,
    workflow: dict,
    tags: list[str],
    delivery_id: str,
) -> tuple[Settings, datetime, respx.Route, respx.Route, respx.Route]:
    """Run one workflow scenario with the shared Zammad route setup."""
    settings = _settings(str(tmp_path), workflow=workflow)
    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(ticket_pipeline_module, "now_utc", lambda: fixed_now)
    payload = {"ticket": {"id": 123}, "_request_id": delivery_id}

    with respx.mock:
        _mock_ticket(ticket_id=123)
        _mock_articles(ticket_id=123)
        respx.get(
            "https://zammad.example.local/api/v1/tags",
            params={"object": "Ticket", "o_id": "123"},
        ).mock(return_value=httpx.Response(200, json=tags))
        remove_tag_route, add_tag_route = _mock_tag_routes()
        article_route = respx.post("https://zammad.example.local/api/v1/ticket_articles").mock(
            return_value=httpx.Response(
                200,
                json={"id": 999, "internal": True, "subject": "ok", "body": "<p>ok</p>"},
            )
        )
        asyncio.run(process_ticket(delivery_id, payload, settings))

    return settings, fixed_now, remove_tag_route, add_tag_route, article_route


def test_workflow_trigger_tag_is_respected(tmp_path, monkeypatch) -> None:
    settings, fixed_now, remove_tag_route, add_tag_route, _article_route = _run_workflow(
        tmp_path,
        monkeypatch,
        workflow={"trigger_tag": "pdf:archive"},
        tags=["pdf:archive"],
        delivery_id="delivery-workflow-1",
    )
    removed = _called_tag_items(remove_tag_route)
    added = _called_tag_items(add_tag_route)
    _assert_success_tags(removed, added, trigger_tag="pdf:archive")
    _assert_archive_exists(tmp_path, settings, fixed_now)


def test_workflow_require_tag_can_be_disabled(tmp_path, monkeypatch) -> None:
    settings, fixed_now, _remove_tag_route, _add_tag_route, _article_route = _run_workflow(
        tmp_path,
        monkeypatch,
        workflow={"require_tag": False},
        tags=[],
        delivery_id="delivery-workflow-2",
    )
    _assert_archive_exists(tmp_path, settings, fixed_now)


def test_workflow_acknowledge_on_success_can_be_disabled(tmp_path, monkeypatch) -> None:
    _settings_result, _fixed_now, _remove_tag_route, _add_tag_route, article_route = _run_workflow(
        tmp_path,
        monkeypatch,
        workflow={"acknowledge_on_success": False},
        tags=["pdf:sign"],
        delivery_id="delivery-workflow-3",
    )
    assert article_route.call_count == 0
