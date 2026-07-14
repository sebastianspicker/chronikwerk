from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from datetime import UTC, datetime

import httpx
import respx

from zammad_pdf_archiver.app.jobs import ticket_stores
from zammad_pdf_archiver.app.jobs.shutdown import clear_shutting_down, wait_for_tasks
from zammad_pdf_archiver.app.server import create_app
from zammad_pdf_archiver.config.load import load_settings
from zammad_pdf_archiver.domain.state_machine import (
    DONE_TAG,
    ERROR_TAG,
    PROCESSING_TAG,
    TRIGGER_TAG,
)

SECRET = "test-webhook-hmac-secret-0123456789abcdef"
ZAMMAD__BASE_URL = "https://zammad.example.local"


def _sign(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _called_tag_items(route: respx.Route) -> list[str]:
    items: list[str] = []
    for call in route.calls:
        body = json.loads(call.request.content.decode("utf-8"))
        items.append(body.get("item"))
    return items


def _create_test_app(tmp_path, monkeypatch):
    clear_shutting_down()
    monkeypatch.setenv("ZAMMAD__BASE_URL", ZAMMAD__BASE_URL)
    monkeypatch.setenv("ZAMMAD__API_TOKEN", "test-token")
    monkeypatch.setenv("STORAGE__ROOT", str(tmp_path))
    monkeypatch.setenv("ZAMMAD__WEBHOOK_HMAC_SECRET", SECRET)
    monkeypatch.setenv("HARDENING__TRANSPORT__ALLOW_PRIVATE_NETWORKS", "true")

    settings = load_settings()
    app = create_app(settings)
    ticket_stores.reset_for_tests()
    return app


async def _post_signed(app, path: str, body: bytes, delivery_id: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            path,
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature": _sign(body, SECRET),
                "X-Zammad-Delivery": delivery_id,
            },
        )
        await wait_for_tasks(timeout=5.0)
        return response


def _body(payload) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _ticket_json(
    ticket_id: int,
    *,
    number: str = "20240123",
    owner: str = "agent",
    fallback: str = "fallback-agent",
    archive_path=None,
) -> dict[str, object]:
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


def _article_json() -> dict[str, object]:
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


def _register_ticket(zammad, ticket_id: int, json_body: dict[str, object]) -> respx.Route:
    return zammad.get(f"{ZAMMAD__BASE_URL}/api/v1/tickets/{ticket_id}").mock(
        return_value=httpx.Response(200, json=json_body)
    )


def _register_tags(zammad, ticket_id: int) -> respx.Route:
    return zammad.get(
        f"{ZAMMAD__BASE_URL}/api/v1/tags",
        params={"object": "Ticket", "o_id": str(ticket_id)},
    ).mock(return_value=httpx.Response(200, json=[TRIGGER_TAG]))


def _register_articles(zammad, ticket_id: int, articles: list[dict[str, object]]) -> respx.Route:
    return zammad.get(f"{ZAMMAD__BASE_URL}/api/v1/ticket_articles/by_ticket/{ticket_id}").mock(
        return_value=httpx.Response(200, json=articles)
    )


def _register_mutation_routes(zammad) -> tuple[respx.Route, respx.Route, respx.Route]:
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
    import zammad_pdf_archiver.app.jobs.process_ticket as process_ticket_module

    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(process_ticket_module, "now_utc", lambda: fixed_now)
    return fixed_now


def test_e2e_smoke_ingest_happy_path_writes_pdf_and_updates_zammad(tmp_path, monkeypatch) -> None:
    app = _create_test_app(tmp_path, monkeypatch)
    fixed_now = _set_fixed_now(monkeypatch)

    payload = {"ticket": {"id": 123}, "user": {"login": "agent-from-webhook"}}
    body = _body(payload)

    with respx.mock(assert_all_called=True) as zammad:
        ticket_route = _register_ticket(zammad, 123, _ticket_json(123))
        tags_route = _register_tags(zammad, 123)
        _register_articles(zammad, 123, [_article_json()])
        remove_tag_route, add_tag_route, article_route = _register_mutation_routes(zammad)

        response = asyncio.run(
            _post_signed(app, "/ingest", body, "delivery-smoke-e2e-20260207-0001")
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

        added = _called_tag_items(add_tag_route)
        removed = _called_tag_items(remove_tag_route)

        assert PROCESSING_TAG in added
        assert DONE_TAG in added
        assert ERROR_TAG not in added

        assert TRIGGER_TAG in removed
        assert ERROR_TAG in removed
        assert PROCESSING_TAG in removed


def test_e2e_smoke_ingest_duplicate_delivery_id_is_idempotent(tmp_path, monkeypatch) -> None:
    app = _create_test_app(tmp_path, monkeypatch)
    _set_fixed_now(monkeypatch)

    payload = {"ticket": {"id": 123}, "user": {"login": "agent-from-webhook"}}
    body = _body(payload)

    with respx.mock(assert_all_called=True) as zammad:
        ticket_route = _register_ticket(zammad, 123, _ticket_json(123))
        tags_route = _register_tags(zammad, 123)
        _register_articles(zammad, 123, [])
        _, _, article_route = _register_mutation_routes(zammad)

        first = asyncio.run(_post_signed(app, "/ingest", body, "delivery-smoke-dedupe-1"))
        second = asyncio.run(_post_signed(app, "/ingest", body, "delivery-smoke-dedupe-1"))

        assert first.status_code == 202
        assert second.status_code == 202
        assert first.json() == {"status": "accepted", "ticket_id": 123}
        assert second.json() == {"status": "accepted", "ticket_id": 123}

        assert ticket_route.call_count == 1
        assert tags_route.call_count == 1
        assert article_route.call_count == 1


def test_e2e_smoke_batch_duplicate_delivery_id_is_idempotent(tmp_path, monkeypatch) -> None:
    app = _create_test_app(tmp_path, monkeypatch)

    payloads = [
        {"ticket": {"id": 101}, "user": {"login": "agent-101"}},
        {"ticket": {"id": 202}, "user": {"login": "agent-202"}},
    ]
    body = _body(payloads)

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

        first = asyncio.run(_post_signed(app, "/ingest/batch", body, "delivery-smoke-batch-1"))
        second = asyncio.run(_post_signed(app, "/ingest/batch", body, "delivery-smoke-batch-1"))

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
