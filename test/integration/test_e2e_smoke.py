from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any

import httpx
import respx

from test.support.checks import check
from test.support.credentials import fake_credential
from test.support.integration_helpers import (
    assert_success_tag_transitions,
    called_tag_items,
)
from test.support.time_control import freeze_process_ticket_now
from zammad_pdf_archiver.app.jobs import ticket_stores
from zammad_pdf_archiver.app.server import create_app
from zammad_pdf_archiver.config.load import load_settings
from zammad_pdf_archiver.domain.state_machine import (
    TRIGGER_TAG,
)


def _sign(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha1).hexdigest()
    return f"sha1={digest}"


def _called_tag_items(route: respx.Route) -> list[str]:
    return called_tag_items(route)


def _create_smoke_app(tmp_path, monkeypatch) -> tuple[str, Any]:
    secret = fake_credential("test-secret")
    monkeypatch.setenv("ZAMMAD_BASE_URL", "https://zammad.example.local")
    monkeypatch.setenv("ZAMMAD_API_TOKEN", fake_credential("test-token"))
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("WEBHOOK_HMAC_SECRET", secret)
    return secret, create_app(load_settings())


def _json_body(payload: object) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _prepare_single_ticket_smoke(tmp_path, monkeypatch) -> tuple[str, Any, bytes]:
    secret, app = _create_smoke_app(tmp_path, monkeypatch)

    import zammad_pdf_archiver.app.jobs.process_ticket as process_ticket_module

    ticket_stores._reset_for_tests()
    freeze_process_ticket_now(
        monkeypatch,
        process_ticket_module,
        datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC),
    )
    body = _json_body({"ticket": {"id": 123}, "user": {"login": "agent-from-webhook"}})
    return secret, app, body


async def _post_signed(
    *,
    app: Any,
    path: str,
    body: bytes,
    secret: str,
    delivery_id: str,
) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(
            path,
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature": _sign(body, secret),
                "X-Zammad-Delivery": delivery_id,
            },
        )


def _post_ingest(app: Any, *, body: bytes, secret: str, delivery_id: str) -> httpx.Response:
    return asyncio.run(
        _post_signed(
            app=app,
            path="/ingest",
            body=body,
            secret=secret,
            delivery_id=delivery_id,
        )
    )


def _ticket_response(
    *,
    ticket_id: int,
    number: str,
    owner: str,
    archive_path: list[str],
) -> dict[str, object]:
    return {
        "id": ticket_id,
        "number": number,
        "owner": {"login": owner},
        "updated_by": {"login": f"fallback-{owner}"},
        "preferences": {
            "custom_fields": {
                "archive_user_mode": "owner",
                "archive_path": archive_path,
            }
        },
    }


def _mock_single_ticket_reads(zammad, *, articles: list[dict[str, object]] | None = None):
    return _mock_ticket_reads(
        zammad,
        ticket_id=123,
        ticket_json=_ticket_response(
            ticket_id=123,
            number="20240123",
            owner="agent",
            archive_path=["A", "B", "C"],
        ),
        articles=articles,
    )


def _article_payload() -> dict[str, object]:
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


def _mock_ticket_reads(
    zammad,
    *,
    ticket_id: int,
    ticket_json: dict[str, object],
    articles: list[dict[str, object]] | None = None,
) -> tuple[respx.Route, respx.Route, respx.Route]:
    ticket_route = zammad.get(f"https://zammad.example.local/api/v1/tickets/{ticket_id}").mock(
        return_value=httpx.Response(200, json=ticket_json)
    )
    tags_route = zammad.get(
        "https://zammad.example.local/api/v1/tags",
        params={"object": "Ticket", "o_id": str(ticket_id)},
    ).mock(return_value=httpx.Response(200, json=[TRIGGER_TAG]))
    articles_route = zammad.get(
        f"https://zammad.example.local/api/v1/ticket_articles/by_ticket/{ticket_id}"
    ).mock(return_value=httpx.Response(200, json=[] if articles is None else articles))
    return ticket_route, tags_route, articles_route


def _mock_zammad_writes(zammad) -> tuple[respx.Route, respx.Route, respx.Route]:
    remove_tag_route = zammad.post("https://zammad.example.local/api/v1/tags/remove").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    add_tag_route = zammad.post("https://zammad.example.local/api/v1/tags/add").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    article_route = zammad.post("https://zammad.example.local/api/v1/ticket_articles").mock(
        return_value=httpx.Response(200, json={"id": 999, "internal": True, "subject": "ok"})
    )
    return remove_tag_route, add_tag_route, article_route


def _mock_batch_ticket_reads(
    zammad,
) -> tuple[respx.Route, respx.Route, respx.Route, respx.Route, respx.Route, respx.Route]:
    ticket_101, tags_101, articles_101 = _mock_ticket_reads(
        zammad,
        ticket_id=101,
        ticket_json=_ticket_response(
            ticket_id=101,
            number="20240101",
            owner="agent-101",
            archive_path=["A"],
        ),
    )
    ticket_202, tags_202, articles_202 = _mock_ticket_reads(
        zammad,
        ticket_id=202,
        ticket_json=_ticket_response(
            ticket_id=202,
            number="20240102",
            owner="agent-202",
            archive_path=["B"],
        ),
    )
    return ticket_101, ticket_202, tags_101, tags_202, articles_101, articles_202


def _assert_accepted(response: httpx.Response, expected_json: dict[str, object]) -> None:
    check(not not response.status_code == 202, "assertion failed")
    check(not not response.json() == expected_json, "assertion failed")


def _assert_batch_routes_called_once(
    *,
    ticket_101: respx.Route,
    ticket_202: respx.Route,
    tags_101: respx.Route,
    tags_202: respx.Route,
    articles_101: respx.Route,
    articles_202: respx.Route,
    article_route: respx.Route,
) -> None:
    check(not not ticket_101.call_count == 1, "assertion failed")
    check(not not ticket_202.call_count == 1, "assertion failed")
    check(not not tags_101.call_count == 1, "assertion failed")
    check(not not tags_202.call_count == 1, "assertion failed")
    check(not not articles_101.call_count == 1, "assertion failed")
    check(not not articles_202.call_count == 1, "assertion failed")
    check(not not article_route.call_count == 2, "assertion failed")


def test_e2e_smoke_ingest_happy_path_writes_pdf_and_updates_zammad(tmp_path, monkeypatch) -> None:
    secret, app, body = _prepare_single_ticket_smoke(tmp_path, monkeypatch)

    with respx.mock(assert_all_called=True) as zammad:
        ticket_route, tags_route, _ = _mock_single_ticket_reads(
            zammad,
            articles=[_article_payload()],
        )
        remove_tag_route, add_tag_route, article_route = _mock_zammad_writes(zammad)

        response = _post_ingest(
            app,
            body=body,
            secret=secret,
            delivery_id="delivery-smoke-e2e-20260207-0001",
        )
        _assert_accepted(response, {"status": "accepted", "ticket_id": 123})

        expected_path = tmp_path / "agent" / "A" / "B" / "C" / "Ticket-20240123_2026-02-07.pdf"
        check(not not expected_path.exists(), "assertion failed")
        check(not not expected_path.read_bytes().startswith(b"%PDF"), "assertion failed")

        check(not not ticket_route.called, "assertion failed")
        check(not not tags_route.called, "assertion failed")
        check(not not article_route.called, "assertion failed")

        assert_success_tag_transitions(
            add_tag_route=add_tag_route,
            remove_tag_route=remove_tag_route,
        )


def test_e2e_smoke_ingest_duplicate_delivery_id_is_idempotent(tmp_path, monkeypatch) -> None:
    secret, app, body = _prepare_single_ticket_smoke(tmp_path, monkeypatch)

    with respx.mock(assert_all_called=True) as zammad:
        ticket_route, tags_route, _ = _mock_single_ticket_reads(zammad)
        _, _, article_route = _mock_zammad_writes(zammad)

        first = _post_ingest(
            app,
            body=body,
            secret=secret,
            delivery_id="delivery-smoke-dedupe-1",
        )
        second = _post_ingest(
            app,
            body=body,
            secret=secret,
            delivery_id="delivery-smoke-dedupe-1",
        )

        _assert_accepted(first, {"status": "accepted", "ticket_id": 123})
        _assert_accepted(second, {"status": "accepted", "ticket_id": 123})

        check(not not ticket_route.call_count == 1, "assertion failed")
        check(not not tags_route.call_count == 1, "assertion failed")
        check(not not article_route.call_count == 1, "assertion failed")


def test_e2e_smoke_batch_duplicate_delivery_id_is_idempotent(tmp_path, monkeypatch) -> None:
    secret, app = _create_smoke_app(tmp_path, monkeypatch)

    ticket_stores._reset_for_tests()

    payloads = [
        {"ticket": {"id": 101}, "user": {"login": "agent-101"}},
        {"ticket": {"id": 202}, "user": {"login": "agent-202"}},
    ]
    body = _json_body(payloads)

    with respx.mock(assert_all_called=True) as zammad:
        ticket_101, ticket_202, tags_101, tags_202, articles_101, articles_202 = (
            _mock_batch_ticket_reads(zammad)
        )
        _, _, article_route = _mock_zammad_writes(zammad)

        first = asyncio.run(
            _post_signed(
                app=app,
                path="/ingest/batch",
                body=body,
                secret=secret,
                delivery_id="delivery-smoke-batch-1",
            )
        )
        second = asyncio.run(
            _post_signed(
                app=app,
                path="/ingest/batch",
                body=body,
                secret=secret,
                delivery_id="delivery-smoke-batch-1",
            )
        )

        _assert_accepted(first, {"status": "accepted", "count": 2})
        _assert_accepted(second, {"status": "accepted", "count": 2})

        _assert_batch_routes_called_once(
            ticket_101=ticket_101,
            ticket_202=ticket_202,
            tags_101=tags_101,
            tags_202=tags_202,
            articles_101=articles_101,
            articles_202=articles_202,
            article_route=article_route,
        )
