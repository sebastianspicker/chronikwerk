from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from fastapi.testclient import TestClient

from test.support.process_ticket_helpers import noop_process_ticket
from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.constants import FORCE_REPROCESS_KEY
from zammad_pdf_archiver.app.server import create_app
from zammad_pdf_archiver.config.settings import Settings

_TEST_WEBHOOK_SECRET = "test-webhook-secret"


def _test_settings(storage_root: str, *, overrides: dict[str, Any] | None = None) -> Settings:
    return make_settings(storage_root, secret=_TEST_WEBHOOK_SECRET, overrides=overrides)


def _signed_headers(body: bytes, headers: dict[str, str] | None = None) -> dict[str, str]:
    digest = hmac.new(
        _TEST_WEBHOOK_SECRET.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    signed = {
        "Content-Type": "application/json",
        "X-Hub-Signature": f"sha256={digest}",
    }
    if headers:
        signed.update(headers)
    return signed


def _post_signed(
    client: TestClient,
    path: str,
    payload: Any,
    *,
    headers: dict[str, str] | None = None,
):
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return client.post(path, content=body, headers=_signed_headers(body, headers))


def _test_settings_require_delivery_id(storage_root: str) -> Settings:
    return make_settings(
        storage_root,
        secret=_TEST_WEBHOOK_SECRET,
        require_delivery_id=True,
        overrides={"workflow": {"delivery_id_ttl_seconds": 3600}},
    )


def test_ingest_accepts_and_extracts_ticket_id(tmp_path, monkeypatch) -> None:
    calls: list[tuple[object, object, object]] = []

    async def _stub_process_ticket(delivery_id, payload, settings) -> None:
        calls.append((delivery_id, payload, settings))

    app = create_app(_test_settings(str(tmp_path)))
    import zammad_pdf_archiver.app.routes.ingest as ingest_route

    monkeypatch.setattr(ingest_route, "process_ticket", _stub_process_ticket)
    client = TestClient(app)

    response = _post_signed(client, "/ingest", {"ticket": {"id": 123}})
    assert response.status_code == 202
    assert response.json() == {"status": "accepted", "ticket_id": 123}
    assert response.headers.get("X-Request-Id")
    assert len(calls) == 1


def test_ingest_rejects_payload_without_ticket_id(tmp_path) -> None:
    """Schema validation: payload must contain ticket.id or ticket_id (422)."""
    app = create_app(_test_settings(str(tmp_path)))
    client = TestClient(app)

    response = _post_signed(client, "/ingest", {})
    assert response.status_code == 422


def test_request_id_header_is_preserved(tmp_path, monkeypatch) -> None:
    app = create_app(_test_settings(str(tmp_path)))
    import zammad_pdf_archiver.app.routes.ingest as ingest_route

    monkeypatch.setattr(ingest_route, "process_ticket", noop_process_ticket)
    client = TestClient(app)

    response = _post_signed(
        client,
        "/ingest",
        {"ticket": {"id": 1}},
        headers={"X-Request-Id": "test-req-id"},
    )
    assert response.status_code == 202
    assert response.headers["X-Request-Id"] == "test-req-id"


def test_request_id_header_invalid_value_is_replaced(tmp_path, monkeypatch) -> None:
    app = create_app(_test_settings(str(tmp_path)))
    import zammad_pdf_archiver.app.routes.ingest as ingest_route

    monkeypatch.setattr(ingest_route, "process_ticket", noop_process_ticket)
    client = TestClient(app)

    response = _post_signed(
        client,
        "/ingest",
        {"ticket": {"id": 1}},
        headers={"X-Request-Id": "bad value with spaces"},
    )
    assert response.status_code == 202
    assert response.headers["X-Request-Id"] != "bad value with spaces"
    assert response.headers["X-Request-Id"]


def test_ingest_passes_delivery_id_header_to_process_ticket(tmp_path, monkeypatch) -> None:
    calls: list[tuple[str | None, dict[str, Any], Settings]] = []

    async def _stub_process_ticket(
        delivery_id: str | None, payload: dict[str, Any], settings: Settings
    ) -> None:
        calls.append((delivery_id, payload, settings))

    app = create_app(_test_settings(str(tmp_path)))
    import zammad_pdf_archiver.app.routes.ingest as ingest_route

    monkeypatch.setattr(ingest_route, "process_ticket", _stub_process_ticket)
    client = TestClient(app)

    response = _post_signed(
        client,
        "/ingest",
        {"ticket": {"id": 123}},
        headers={"X-Zammad-Delivery": "delivery-xyz"},
    )
    assert response.status_code == 202
    assert len(calls) == 1

    delivery_id, payload, _settings = calls[0]
    assert delivery_id == "delivery-xyz"
    assert payload["ticket"]["id"] == 123
    assert isinstance(payload.get("_request_id"), str)
    assert payload["_request_id"]
    assert FORCE_REPROCESS_KEY not in payload


def test_ingest_ignores_force_reprocess_field_from_public_payload(tmp_path, monkeypatch) -> None:
    calls: list[tuple[str | None, dict[str, Any], Settings]] = []

    async def _stub_process_ticket(
        delivery_id: str | None, payload: dict[str, Any], settings: Settings
    ) -> None:
        calls.append((delivery_id, payload, settings))

    app = create_app(_test_settings(str(tmp_path)))
    import zammad_pdf_archiver.app.routes.ingest as ingest_route

    monkeypatch.setattr(ingest_route, "process_ticket", _stub_process_ticket)
    client = TestClient(app)

    response = _post_signed(
        client,
        "/ingest",
        {"ticket": {"id": 123}, FORCE_REPROCESS_KEY: True},
    )
    assert response.status_code == 202
    assert len(calls) == 1
    assert FORCE_REPROCESS_KEY not in calls[0][1]


def test_ingest_rejects_missing_delivery_id_when_required(tmp_path) -> None:
    app = create_app(_test_settings_require_delivery_id(str(tmp_path)))
    client = TestClient(app)

    response = _post_signed(client, "/ingest", {"ticket": {"id": 123}})
    assert response.status_code == 400
    assert response.json() == {"detail": "missing_delivery_id", "code": "missing_delivery_id"}
    assert response.headers.get("X-Request-Id")


def test_ingest_rejects_invalid_ticket_id_type(tmp_path, monkeypatch) -> None:
    """Schema validation: ticket.id must be a positive int (422); no background run."""
    calls: list[tuple[str | None, dict[str, Any], Settings]] = []

    async def _stub_process_ticket(
        delivery_id: str | None, payload: dict[str, Any], settings: Settings
    ) -> None:
        calls.append((delivery_id, payload, settings))

    app = create_app(_test_settings(str(tmp_path)))
    import zammad_pdf_archiver.app.routes.ingest as ingest_route

    monkeypatch.setattr(ingest_route, "process_ticket", _stub_process_ticket)
    client = TestClient(app)

    response = _post_signed(client, "/ingest", {"ticket": {"id": True}})
    assert response.status_code == 422
    assert calls == []


def test_ingest_batch_uses_per_item_delivery_ids_when_header_present(tmp_path, monkeypatch) -> None:
    calls: list[tuple[str | None, dict[str, Any], Settings]] = []

    async def _stub_process_ticket(
        delivery_id: str | None, payload: dict[str, Any], settings: Settings
    ) -> None:
        calls.append((delivery_id, payload, settings))

    app = create_app(_test_settings(str(tmp_path)))
    import zammad_pdf_archiver.app.routes.ingest as ingest_route

    monkeypatch.setattr(ingest_route, "process_ticket", _stub_process_ticket)
    client = TestClient(app)

    response = _post_signed(
        client,
        "/ingest/batch",
        [
            {"ticket": {"id": 111}},
            {"ticket_id": 222},
        ],
        headers={"X-Zammad-Delivery": "delivery-batch-xyz"},
    )
    assert response.status_code == 202
    assert response.json() == {"status": "accepted", "count": 2}
    assert len(calls) == 2
    assert calls[0][0] == "delivery-batch-xyz:0"
    assert calls[1][0] == "delivery-batch-xyz:1"
    assert calls[0][1]["ticket"]["id"] == 111
    assert calls[1][1]["ticket_id"] == 222


def test_batch_ingest_ignores_force_reprocess_flag_from_public_payload(
    tmp_path, monkeypatch
) -> None:
    calls: list[tuple[str | None, dict[str, Any], Settings]] = []

    async def _stub_process_ticket(
        delivery_id: str | None, payload: dict[str, Any], settings: Settings
    ) -> None:
        calls.append((delivery_id, payload, settings))

    app = create_app(_test_settings(str(tmp_path)))
    import zammad_pdf_archiver.app.routes.ingest as ingest_route

    monkeypatch.setattr(ingest_route, "process_ticket", _stub_process_ticket)
    client = TestClient(app)

    response = _post_signed(
        client,
        "/ingest/batch",
        [
            {"ticket": {"id": 111}, FORCE_REPROCESS_KEY: True},
            {"ticket_id": 222, FORCE_REPROCESS_KEY: True},
        ],
    )
    assert response.status_code == 202
    assert len(calls) == 2
    assert FORCE_REPROCESS_KEY not in calls[0][1]
    assert FORCE_REPROCESS_KEY not in calls[1][1]


def _test_settings_with_retry_token(storage_root: str, **extra_overrides: Any) -> Settings:
    overrides: dict[str, Any] = {
        "retry_bearer_token": "test-retry-token",
    }
    if extra_overrides:
        for key, val in extra_overrides.items():
            overrides[key] = val
    return make_settings(storage_root, overrides=overrides)


def test_retry_endpoint_accepts_ticket_id(tmp_path, monkeypatch) -> None:
    calls: list[tuple[str | None, dict[str, Any], Settings]] = []

    async def _stub_process_ticket(
        delivery_id: str | None, payload: dict[str, Any], settings: Settings
    ) -> None:
        calls.append((delivery_id, payload, settings))

    app = create_app(_test_settings_with_retry_token(str(tmp_path)))
    import zammad_pdf_archiver.app.routes.ingest as ingest_route

    monkeypatch.setattr(ingest_route, "process_ticket", _stub_process_ticket)
    client = TestClient(app)

    response = client.post(
        "/retry/987",
        headers={"Authorization": "Bearer test-retry-token"},
    )
    assert response.status_code == 202
    assert response.json() == {"status": "accepted", "ticket_id": 987}
    assert len(calls) == 1
    assert calls[0][0] is None
    assert calls[0][1]["ticket_id"] == 987
    assert calls[0][1][FORCE_REPROCESS_KEY] is True


def test_retry_requires_auth(tmp_path) -> None:
    """POST /retry/{ticket_id} without Authorization header returns 401."""
    app = create_app(_test_settings_with_retry_token(str(tmp_path)))
    client = TestClient(app)

    response = client.post("/retry/123")
    assert response.status_code == 401
    assert response.json()["detail"] == "unauthorized"


def test_retry_with_valid_token(tmp_path, monkeypatch) -> None:
    """POST /retry/{ticket_id} with valid bearer token returns 202."""
    calls: list[tuple[str | None, dict[str, Any], Settings]] = []

    async def _stub_process_ticket(
        delivery_id: str | None, payload: dict[str, Any], settings: Settings
    ) -> None:
        calls.append((delivery_id, payload, settings))

    app = create_app(_test_settings_with_retry_token(str(tmp_path)))
    import zammad_pdf_archiver.app.routes.ingest as ingest_route

    monkeypatch.setattr(ingest_route, "process_ticket", _stub_process_ticket)
    client = TestClient(app)

    response = client.post(
        "/retry/123",
        headers={"Authorization": "Bearer test-retry-token"},
    )
    assert response.status_code == 202
    assert response.json() == {"status": "accepted", "ticket_id": 123}
    assert len(calls) == 1
    assert calls[0][1][FORCE_REPROCESS_KEY] is True


def test_retry_with_invalid_token(tmp_path) -> None:
    """POST /retry/{ticket_id} with wrong bearer token returns 401."""
    app = create_app(_test_settings_with_retry_token(str(tmp_path)))
    client = TestClient(app)

    response = client.post(
        "/retry/123",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "unauthorized"


def test_batch_ingest_exceeds_max_size(tmp_path, monkeypatch) -> None:
    """POST /ingest/batch with 101 items returns 422 (batch too large)."""

    async def _stub_process_ticket(
        delivery_id: str | None, payload: dict[str, Any], settings: Settings
    ) -> None:
        pass  # pragma: no cover — should never be called

    app = create_app(_test_settings(str(tmp_path)))
    import zammad_pdf_archiver.app.routes.ingest as ingest_route

    monkeypatch.setattr(ingest_route, "process_ticket", _stub_process_ticket)
    client = TestClient(app)

    payloads = [{"ticket_id": i} for i in range(1, 102)]  # 101 items
    response = _post_signed(client, "/ingest/batch", payloads)
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "batch_too_large"


def test_batch_ingest_at_max_size(tmp_path, monkeypatch) -> None:
    """POST /ingest/batch with exactly 100 items is accepted (202)."""
    calls: list[tuple[str | None, dict[str, Any], Settings]] = []

    async def _stub_process_ticket(
        delivery_id: str | None, payload: dict[str, Any], settings: Settings
    ) -> None:
        calls.append((delivery_id, payload, settings))

    app = create_app(_test_settings(str(tmp_path)))
    import zammad_pdf_archiver.app.routes.ingest as ingest_route

    monkeypatch.setattr(ingest_route, "process_ticket", _stub_process_ticket)
    client = TestClient(app)

    payloads = [{"ticket_id": i} for i in range(1, 101)]  # exactly 100 items
    response = _post_signed(client, "/ingest/batch", payloads)
    assert response.status_code == 202
    assert response.json() == {"status": "accepted", "count": 100}
    assert len(calls) == 100
