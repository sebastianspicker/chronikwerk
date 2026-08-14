"""Exercises the complete signed-ingest-to-archive flow through real application boundaries."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx
import respx

from chronikwerk.app.jobs import ticket_stores
from chronikwerk.app.jobs.shutdown import clear_shutting_down, wait_for_tasks
from chronikwerk.app.server import create_app
from chronikwerk.domain.state_machine import (
    DONE_TAG,
    ERROR_TAG,
    PROCESSING_TAG,
    TRIGGER_TAG,
)
from tests.support.http_security_test_helpers import post_signed_json
from tests.support.settings_factory import make_settings
from tests.support.zammad_client_helpers import called_tag_items
from tests.support.zammad_fixtures import html_article_json

SECRET = "test-webhook-hmac-secret-0123456789abcdef"
ZAMMAD__BASE_URL = "https://zammad.example.local"


def _create_test_app(tmp_path):
    """Create an isolated application with deterministic background dependencies."""
    clear_shutting_down()
    settings = make_settings(str(tmp_path), secret=SECRET)
    app = create_app(settings)
    ticket_stores.reset_for_tests()
    return app


async def _post_signed(app, path: str, payload, delivery_id: str) -> httpx.Response:
    """Submit a correctly signed test request to the application."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await post_signed_json(
            client,
            path,
            payload,
            secret=SECRET,
            delivery_id=delivery_id,
        )
        await wait_for_tasks(timeout=5.0)
        return response


def _ticket_json(
    ticket_id: int,
    *,
    number: str = "20240123",
    owner: str = "agent",
    fallback: str = "fallback-agent",
    archive_path=None,
) -> dict[str, object]:
    """Return a representative Zammad ticket API response."""
    return {
        "id": ticket_id,
        "number": number,
        "owner": {"login": owner},
        "updated_by": {"login": fallback},
        "preferences": {
            "custom_fields": {
                "archive_user_mode": "owner",
                "archive_path": archive_path or ["A", "B", "C"],
            }
        },
    }


def _register_ticket(zammad, ticket_id: int, json_body: dict[str, object]) -> respx.Route:
    """Stub one ticket payload and retain the route for call assertions."""
    return zammad.get(f"{ZAMMAD__BASE_URL}/api/v1/tickets/{ticket_id}").mock(
        return_value=httpx.Response(200, json=json_body)
    )


def _register_tags(zammad, ticket_id: int) -> respx.Route:
    """Stub the trigger-tag lookup required to admit the ticket."""
    return zammad.get(
        f"{ZAMMAD__BASE_URL}/api/v1/tags",
        params={"object": "Ticket", "o_id": str(ticket_id)},
    ).mock(return_value=httpx.Response(200, json=[TRIGGER_TAG]))


def _register_articles(zammad, ticket_id: int, articles: list[dict[str, object]]) -> respx.Route:
    """Stub the article collection used to build the archival snapshot."""
    return zammad.get(f"{ZAMMAD__BASE_URL}/api/v1/ticket_articles/by_ticket/{ticket_id}").mock(
        return_value=httpx.Response(200, json=articles)
    )


def _register_mutation_routes(zammad) -> tuple[respx.Route, respx.Route, respx.Route]:
    """Stub successful tag mutations and internal-note creation."""
    remove_tag_route = zammad.post(f"{ZAMMAD__BASE_URL}/api/v1/tags/remove").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    add_tag_route = zammad.post(f"{ZAMMAD__BASE_URL}/api/v1/tags/add").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    article_route = zammad.post(f"{ZAMMAD__BASE_URL}/api/v1/ticket_articles").mock(
        return_value=httpx.Response(
            200,
            json={"id": 999, "internal": True, "subject": "ok", "body": "<p>ok</p>"},
        )
    )
    return remove_tag_route, add_tag_route, article_route


def _set_fixed_now(monkeypatch) -> datetime:
    """Pin application time so persisted artifact names remain predictable."""
    from chronikwerk.app.jobs import _ticket_pipeline as ticket_pipeline_module

    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(ticket_pipeline_module, "now_utc", lambda: fixed_now)
    return fixed_now


def test_e2e_smoke_ingest_happy_path_writes_pdf_and_updates_zammad(tmp_path, monkeypatch) -> None:
    app = _create_test_app(tmp_path)
    fixed_now = _set_fixed_now(monkeypatch)

    payload = {"ticket": {"id": 123}, "user": {"login": "agent-from-webhook"}}
    with respx.mock(assert_all_called=True) as zammad:
        ticket_route = _register_ticket(zammad, 123, _ticket_json(123))
        tags_route = _register_tags(zammad, 123)
        _register_articles(zammad, 123, [html_article_json()])
        remove_tag_route, add_tag_route, article_route = _register_mutation_routes(zammad)

        response = asyncio.run(
            _post_signed(app, "/ingest", payload, "delivery-smoke-e2e-20260207-0001")
        )

        assert response.status_code == 202
        assert response.json() == {"status": "accepted", "ticket_id": 123}

        date_iso = fixed_now.date().isoformat()
        expected_path = tmp_path / "agent" / "A" / "B" / "C" / f"Ticket-20240123_{date_iso}.pdf"
        assert expected_path.exists()
        assert expected_path.read_bytes().startswith(b"%PDF")

        assert ticket_route.called
        assert tags_route.called
        assert article_route.called

        added = called_tag_items(add_tag_route)
        removed = called_tag_items(remove_tag_route)

        assert PROCESSING_TAG in added
        assert DONE_TAG in added
        assert ERROR_TAG not in added

        assert TRIGGER_TAG in removed
        assert ERROR_TAG in removed
        assert PROCESSING_TAG in removed


def test_e2e_smoke_ingest_duplicate_delivery_id_is_idempotent(tmp_path, monkeypatch) -> None:
    app = _create_test_app(tmp_path)
    _set_fixed_now(monkeypatch)

    payload = {"ticket": {"id": 123}, "user": {"login": "agent-from-webhook"}}
    with respx.mock(assert_all_called=True) as zammad:
        ticket_route = _register_ticket(zammad, 123, _ticket_json(123))
        tags_route = _register_tags(zammad, 123)
        _register_articles(zammad, 123, [])
        _, _, article_route = _register_mutation_routes(zammad)

        first = asyncio.run(_post_signed(app, "/ingest", payload, "delivery-smoke-dedupe-1"))
        second = asyncio.run(_post_signed(app, "/ingest", payload, "delivery-smoke-dedupe-1"))

        assert first.status_code == 202
        assert second.status_code == 202
        assert first.json() == {"status": "accepted", "ticket_id": 123}
        assert second.json() == {"status": "accepted", "ticket_id": 123}

        assert ticket_route.call_count == 1
        assert tags_route.call_count == 1
        assert article_route.call_count == 1


def test_e2e_smoke_batch_duplicate_delivery_id_is_idempotent(tmp_path, monkeypatch) -> None:
    app = _create_test_app(tmp_path)

    payloads = [
        {"ticket": {"id": 101}, "user": {"login": "agent-101"}},
        {"ticket": {"id": 202}, "user": {"login": "agent-202"}},
    ]
    with respx.mock(assert_all_called=True) as zammad:
        ticket_101 = _register_ticket(
            zammad,
            101,
            _ticket_json(
                101,
                number="20240101",
                owner="agent-101",
                fallback="fallback-agent-101",
                archive_path=["A"],
            ),
        )
        ticket_202 = _register_ticket(
            zammad,
            202,
            _ticket_json(
                202,
                number="20240102",
                owner="agent-202",
                fallback="fallback-agent-202",
                archive_path=["B"],
            ),
        )
        tags_101 = _register_tags(zammad, 101)
        tags_202 = _register_tags(zammad, 202)
        articles_101 = _register_articles(zammad, 101, [])
        articles_202 = _register_articles(zammad, 202, [])
        _, _, article_route = _register_mutation_routes(zammad)

        first = asyncio.run(_post_signed(app, "/ingest/batch", payloads, "delivery-smoke-batch-1"))
        second = asyncio.run(_post_signed(app, "/ingest/batch", payloads, "delivery-smoke-batch-1"))

        assert first.status_code == 202
        assert second.status_code == 202
        assert first.json() == {"status": "accepted", "count": 2}
        assert second.json() == {"status": "accepted", "count": 2}

        assert ticket_101.call_count == 1
        assert ticket_202.call_count == 1
        assert tags_101.call_count == 1
        assert tags_202.call_count == 1
        assert articles_101.call_count == 1
        assert articles_202.call_count == 1
        assert article_route.call_count == 2
