from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from typing import Any

import httpx
from fastapi.testclient import TestClient

from test.support.checks import check
from test.support.credentials import fake_credential
from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.constants import FORCE_REPROCESS_KEY
from zammad_pdf_archiver.app.jobs import ticket_stores
from zammad_pdf_archiver.app.jobs.process_ticket import ProcessTicketResult
from zammad_pdf_archiver.app.jobs.shutdown import wait_for_tasks
from zammad_pdf_archiver.app.server import create_app
from zammad_pdf_archiver.config.settings import Settings


def _test_settings(storage_root: str, *, overrides: dict[str, Any] | None = None) -> Settings:
    return make_settings(storage_root, overrides=overrides)


def _test_settings_require_delivery_id(storage_root: str) -> Settings:
    return make_settings(
        storage_root,
        require_delivery_id=True,
        overrides={"workflow": {"delivery_id_ttl_seconds": 3600}},
    )


def _test_settings_signed(
    storage_root: str,
    *,
    require_delivery_id: bool = False,
    overrides: dict[str, Any] | None = None,
) -> Settings:
    return make_settings(
        storage_root,
        secret=fake_credential("test-secret"),
        allow_unsigned=False,
        allow_unsigned_when_no_secret=False,
        require_delivery_id=require_delivery_id,
        overrides=overrides,
    )


def _json_body(payload: Any) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def _signature(body: bytes, secret: str | None = None) -> str:
    if secret is None:
        secret = fake_credential("test-secret")
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _signed_headers(
    body: bytes,
    *,
    delivery_id: str | None = "delivery-test",
    request_id: str | None = None,
    secret: str | None = None,
) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "X-Hub-Signature": _signature(body, secret),
    }
    if delivery_id is not None:
        headers["X-Zammad-Delivery"] = delivery_id
    if request_id is not None:
        headers["X-Request-Id"] = request_id
    return headers


class _FakeRedisDeliveryIdStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.set_calls: list[tuple[str, str, int | None, bool, bool]] = []

    async def set(
        self,
        key: str,
        value: str,
        *,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool:
        claimed = not (nx and key in self.values)
        self.set_calls.append((key, value, ex, nx, claimed))
        if claimed:
            self.values[key] = value
        return claimed

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def delete(self, key: str) -> int:
        existed = key in self.values
        self.values.pop(key, None)
        return int(existed)


def _client_with_stubbed_process_ticket(
    tmp_path,
    monkeypatch,
    *,
    settings: Settings | None = None,
) -> tuple[TestClient, list[tuple[str | None, dict[str, Any], Settings]]]:
    calls: list[tuple[str | None, dict[str, Any], Settings]] = []

    async def _stub_process_ticket(
        delivery_id: str | None,
        payload: dict[str, Any],
        settings: Settings,
    ) -> None:
        calls.append((delivery_id, payload, settings))

    app = create_app(settings or _test_settings(str(tmp_path)))
    import zammad_pdf_archiver.app.routes.ingest as ingest_route

    monkeypatch.setattr(ingest_route, "process_ticket", _stub_process_ticket)
    return TestClient(app), calls


def test_ingest_accepts_and_extracts_ticket_id(tmp_path, monkeypatch) -> None:
    client, calls = _client_with_stubbed_process_ticket(tmp_path, monkeypatch)

    response = client.post("/ingest", json={"ticket": {"id": 123}})
    check(not not response.status_code == 202, "assertion failed")
    check(not not response.json() == {"status": "accepted", "ticket_id": 123}, "assertion failed")
    check(not not response.headers.get("X-Request-Id"), "assertion failed")
    check(not not len(calls) == 1, "assertion failed")


def test_ingest_rejects_payload_without_ticket_id(tmp_path) -> None:
    """Schema validation: payload must contain ticket.id or ticket_id (422)."""
    app = create_app(_test_settings(str(tmp_path)))
    client = TestClient(app)

    response = client.post("/ingest", json={})
    check(not not response.status_code == 422, "assertion failed")


def test_request_id_header_is_preserved(tmp_path, monkeypatch) -> None:
    client, _calls = _client_with_stubbed_process_ticket(tmp_path, monkeypatch)

    response = client.post(
        "/ingest",
        json={"ticket": {"id": 1}},
        headers={"X-Request-Id": "test-req-id"},
    )
    check(not not response.status_code == 202, "assertion failed")
    check(not not response.headers["X-Request-Id"] == "test-req-id", "assertion failed")


def test_request_id_header_invalid_value_is_replaced(tmp_path, monkeypatch) -> None:
    client, _calls = _client_with_stubbed_process_ticket(tmp_path, monkeypatch)

    response = client.post(
        "/ingest",
        json={"ticket": {"id": 1}},
        headers={"X-Request-Id": "bad value with spaces"},
    )
    check(not not response.status_code == 202, "assertion failed")
    check(not not response.headers["X-Request-Id"] != "bad value with spaces", "assertion failed")
    check(not not response.headers["X-Request-Id"], "assertion failed")


def test_ingest_passes_delivery_id_header_to_process_ticket(tmp_path, monkeypatch) -> None:
    client, calls = _client_with_stubbed_process_ticket(tmp_path, monkeypatch)

    response = client.post(
        "/ingest",
        json={"ticket": {"id": 123}},
        headers={"X-Zammad-Delivery": "delivery-xyz"},
    )
    check(not not response.status_code == 202, "assertion failed")
    check(not not len(calls) == 1, "assertion failed")

    delivery_id, payload, _settings = calls[0]
    check(not not delivery_id == "delivery-xyz", "assertion failed")
    check(not not payload["ticket"]["id"] == 123, "assertion failed")
    check(not not isinstance(payload.get("_request_id"), str), "assertion failed")
    check(not not payload["_request_id"], "assertion failed")
    check(not not FORCE_REPROCESS_KEY not in payload, "assertion failed")


def test_ingest_duplicate_delivery_id_uses_redis_idempotency_backend(
    tmp_path,
    monkeypatch: Any,
) -> None:
    ticket_stores._reset_for_tests()

    fake_redis = _FakeRedisDeliveryIdStore()

    async def _fake_get_redis(_redis_url: str) -> _FakeRedisDeliveryIdStore:
        return fake_redis

    processed: list[str | None] = []
    history_statuses: list[str] = []

    async def _stub_process_ticket_with_client(ctx, *, payload):  # noqa: ANN001, ARG001
        processed.append(ctx.delivery_id)
        return ProcessTicketResult(status="processed", ticket_id=ctx.ticket_id)

    async def _stub_record_history(ctx, *, status: str, **kwargs) -> bool:  # noqa: ANN001, ANN003
        history_statuses.append(status)
        return True

    settings = _test_settings(
        str(tmp_path),
        overrides={
            "workflow": {
                "idempotency_backend": "redis",
                "redis_url": "redis://localhost/0",
                "history_retention_maxlen": 0,
            }
        },
    )
    app = create_app(settings)

    import zammad_pdf_archiver.app.jobs.process_ticket as process_ticket_module
    import zammad_pdf_archiver.domain.redis_delivery_id as redis_delivery_id

    monkeypatch.setattr(redis_delivery_id, "get_redis", _fake_get_redis)
    monkeypatch.setattr(
        process_ticket_module,
        "_process_ticket_with_client",
        _stub_process_ticket_with_client,
    )
    monkeypatch.setattr(process_ticket_module, "_record_history", _stub_record_history)

    async def _exercise() -> tuple[httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first = await client.post(
                "/ingest",
                json={"ticket": {"id": 123}},
                headers={"X-Zammad-Delivery": "delivery-redis-dedupe-1"},
            )
            await wait_for_tasks()
            second = await client.post(
                "/ingest",
                json={"ticket": {"id": 123}},
                headers={"X-Zammad-Delivery": "delivery-redis-dedupe-1"},
            )
            await wait_for_tasks()
            return first, second

    first, second = asyncio.run(_exercise())

    check(not not first.status_code == 202, "assertion failed")
    check(not not second.status_code == 202, "assertion failed")
    check(not not processed == ["delivery-redis-dedupe-1"], "assertion failed")
    check(not not history_statuses == ["skipped_idempotency"], "assertion failed")

    delivery_claims = [
        call
        for call in fake_redis.set_calls
        if call[0] == "zammad:delivery_id:delivery-redis-dedupe-1"
    ]
    check(
        not not delivery_claims
        == [
            ("zammad:delivery_id:delivery-redis-dedupe-1", "1", 3600, True, True),
            ("zammad:delivery_id:delivery-redis-dedupe-1", "1", 3600, True, False),
        ],
        "assertion failed",
    )


def test_ingest_ignores_force_reprocess_field_from_public_payload(tmp_path, monkeypatch) -> None:
    client, calls = _client_with_stubbed_process_ticket(tmp_path, monkeypatch)

    response = client.post(
        "/ingest",
        json={"ticket": {"id": 123}, FORCE_REPROCESS_KEY: True},
    )
    check(not not response.status_code == 202, "assertion failed")
    check(not not len(calls) == 1, "assertion failed")
    check(not not FORCE_REPROCESS_KEY not in calls[0][1], "assertion failed")


def test_ingest_rejects_missing_delivery_id_when_required(tmp_path) -> None:
    app = create_app(_test_settings_require_delivery_id(str(tmp_path)))
    client = TestClient(app)

    response = client.post("/ingest", json={"ticket": {"id": 123}})
    check(not not response.status_code == 400, "assertion failed")
    check(
        not not response.json() == {"detail": "missing_delivery_id", "code": "missing_delivery_id"},
        "assertion failed",
    )
    check(not not response.headers.get("X-Request-Id"), "assertion failed")


def test_ingest_rejects_invalid_ticket_id_type(tmp_path, monkeypatch) -> None:
    """Schema validation: ticket.id must be a positive int (422); no background run."""
    client, calls = _client_with_stubbed_process_ticket(tmp_path, monkeypatch)

    response = client.post("/ingest", json={"ticket": {"id": True}})
    check(not not response.status_code == 422, "assertion failed")
    check(not not calls == [], "assertion failed")


def test_ingest_batch_uses_per_item_delivery_ids_when_header_present(tmp_path, monkeypatch) -> None:
    client, calls = _client_with_stubbed_process_ticket(tmp_path, monkeypatch)

    response = client.post(
        "/ingest/batch",
        json=[
            {"ticket": {"id": 111}},
            {"ticket_id": 222},
        ],
        headers={"X-Zammad-Delivery": "delivery-batch-xyz"},
    )
    check(not not response.status_code == 202, "assertion failed")
    check(not not response.json() == {"status": "accepted", "count": 2}, "assertion failed")
    check(not not len(calls) == 2, "assertion failed")
    check(not not calls[0][0] == "delivery-batch-xyz:0", "assertion failed")
    check(not not calls[1][0] == "delivery-batch-xyz:1", "assertion failed")
    check(not not calls[0][1]["ticket"]["id"] == 111, "assertion failed")
    check(not not calls[1][1]["ticket_id"] == 222, "assertion failed")


def test_batch_ingest_ignores_force_reprocess_flag_from_public_payload(
    tmp_path, monkeypatch
) -> None:
    client, calls = _client_with_stubbed_process_ticket(tmp_path, monkeypatch)

    response = client.post(
        "/ingest/batch",
        json=[
            {"ticket": {"id": 111}, FORCE_REPROCESS_KEY: True},
            {"ticket_id": 222, FORCE_REPROCESS_KEY: True},
        ],
    )
    check(not not response.status_code == 202, "assertion failed")
    check(not not len(calls) == 2, "assertion failed")
    check(not not FORCE_REPROCESS_KEY not in calls[0][1], "assertion failed")
    check(not not FORCE_REPROCESS_KEY not in calls[1][1], "assertion failed")
