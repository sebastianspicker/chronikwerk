from __future__ import annotations

import asyncio
from typing import Any

from fastapi.testclient import TestClient

from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.jobs import ticket_stores
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


def test_ingest_accepts_and_extracts_ticket_id(tmp_path, monkeypatch) -> None:
    calls: list[tuple[object, object, object]] = []

    async def _stub_process_ticket(delivery_id, payload, settings) -> None:
        calls.append((delivery_id, payload, settings))

    app = create_app(_test_settings(str(tmp_path)))
    import zammad_pdf_archiver.app.routes.ingest as ingest_route

    monkeypatch.setattr(ingest_route, "process_ticket", _stub_process_ticket)
    client = TestClient(app)

    response = client.post("/ingest", json={"ticket": {"id": 123}})
    assert response.status_code == 202
    assert response.json() == {"status": "accepted", "ticket_id": 123}
    assert response.headers.get("X-Request-Id")
    assert len(calls) == 1


def test_ingest_rejects_payload_without_ticket_id(tmp_path) -> None:
    """Schema validation: payload must contain ticket.id or ticket_id (422)."""
    app = create_app(_test_settings(str(tmp_path)))
    client = TestClient(app)

    response = client.post("/ingest", json={})
    assert response.status_code == 422


def test_request_id_header_is_preserved(tmp_path, monkeypatch) -> None:
    async def _stub_process_ticket(delivery_id, payload, settings) -> None:  # noqa: ANN001, ARG001
        return None

    app = create_app(_test_settings(str(tmp_path)))
    import zammad_pdf_archiver.app.routes.ingest as ingest_route

    monkeypatch.setattr(ingest_route, "process_ticket", _stub_process_ticket)
    client = TestClient(app)

    response = client.post(
        "/ingest",
        json={"ticket": {"id": 1}},
        headers={"X-Request-Id": "test-req-id"},
    )
    assert response.status_code == 202
    assert response.headers["X-Request-Id"] == "test-req-id"


def test_request_id_header_invalid_value_is_replaced(tmp_path, monkeypatch) -> None:
    async def _stub_process_ticket(delivery_id, payload, settings) -> None:  # noqa: ANN001, ARG001
        return None

    app = create_app(_test_settings(str(tmp_path)))
    import zammad_pdf_archiver.app.routes.ingest as ingest_route

    monkeypatch.setattr(ingest_route, "process_ticket", _stub_process_ticket)
    client = TestClient(app)

    response = client.post(
        "/ingest",
        json={"ticket": {"id": 1}},
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

    response = client.post(
        "/ingest",
        json={"ticket": {"id": 123}},
        headers={"X-Zammad-Delivery": "delivery-xyz"},
    )
    assert response.status_code == 202
    assert len(calls) == 1

    delivery_id, payload, _settings = calls[0]
    assert delivery_id == "delivery-xyz"
    assert payload["ticket"]["id"] == 123
    assert isinstance(payload.get("_request_id"), str)
    assert payload["_request_id"]


def test_ingest_rejects_missing_delivery_id_when_required(tmp_path) -> None:
    app = create_app(_test_settings_require_delivery_id(str(tmp_path)))
    client = TestClient(app)

    response = client.post("/ingest", json={"ticket": {"id": 123}})
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

    response = client.post("/ingest", json={"ticket": {"id": True}})
    assert response.status_code == 422
    assert calls == []


def test_ingest_batch_accepts_multiple_payloads(tmp_path, monkeypatch) -> None:
    calls: list[tuple[str | None, dict[str, Any], Settings]] = []

    async def _stub_process_ticket(
        delivery_id: str | None, payload: dict[str, Any], settings: Settings
    ) -> None:
        calls.append((delivery_id, payload, settings))

    app = create_app(_test_settings(str(tmp_path)))
    import zammad_pdf_archiver.app.routes.ingest as ingest_route

    monkeypatch.setattr(ingest_route, "process_ticket", _stub_process_ticket)
    client = TestClient(app)

    response = client.post(
        "/ingest/batch",
        json=[
            {"ticket": {"id": 111}},
            {"ticket_id": 222},
        ],
    )
    assert response.status_code == 202
    assert response.json() == {"status": "accepted", "count": 2}
    assert len(calls) == 2
    assert calls[0][0] is None
    assert calls[1][0] is None
    assert calls[0][1]["ticket"]["id"] == 111
    assert calls[1][1]["ticket_id"] == 222


def _test_settings_with_admin(storage_root: str, **extra_overrides: Any) -> Settings:
    overrides: dict[str, Any] = {
        "admin": {
            "enabled": True,
            "bearer_token": "test-admin-token",
        }
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

    app = create_app(_test_settings_with_admin(str(tmp_path)))
    import zammad_pdf_archiver.app.routes.ingest as ingest_route

    monkeypatch.setattr(ingest_route, "process_ticket", _stub_process_ticket)
    client = TestClient(app)

    response = client.post(
        "/retry/987",
        headers={"Authorization": "Bearer test-admin-token"},
    )
    assert response.status_code == 202
    assert response.json() == {"status": "accepted", "ticket_id": 987}
    assert len(calls) == 1
    assert calls[0][0] is None
    assert calls[0][1]["ticket_id"] == 987


def test_retry_requires_auth(tmp_path) -> None:
    """POST /retry/{ticket_id} without Authorization header returns 401."""
    app = create_app(_test_settings_with_admin(str(tmp_path)))
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

    app = create_app(_test_settings_with_admin(str(tmp_path)))
    import zammad_pdf_archiver.app.routes.ingest as ingest_route

    monkeypatch.setattr(ingest_route, "process_ticket", _stub_process_ticket)
    client = TestClient(app)

    response = client.post(
        "/retry/123",
        headers={"Authorization": "Bearer test-admin-token"},
    )
    assert response.status_code == 202
    assert response.json() == {"status": "accepted", "ticket_id": 123}
    assert len(calls) == 1


def test_retry_with_invalid_token(tmp_path) -> None:
    """POST /retry/{ticket_id} with wrong bearer token returns 401."""
    app = create_app(_test_settings_with_admin(str(tmp_path)))
    client = TestClient(app)

    response = client.post(
        "/retry/123",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "unauthorized"


def test_jobs_endpoint_reports_in_flight_status(tmp_path) -> None:
    ticket_stores.reset_for_tests()
    settings = _test_settings_with_admin(str(tmp_path))
    app = create_app(settings)
    client = TestClient(app)

    acquired = asyncio.run(ticket_stores.try_acquire_ticket(settings, 404))
    assert acquired is True
    try:
        response = client.get(
            "/jobs/404",
            headers={"Authorization": "Bearer test-admin-token"},
        )
        assert response.status_code == 200
        assert response.json() == {"ticket_id": 404, "in_flight": True, "shutting_down": False}
    finally:
        asyncio.run(ticket_stores.release_ticket(settings, 404))
        ticket_stores.reset_for_tests()


def test_ingest_uses_redis_queue_dispatch_when_enabled(tmp_path, monkeypatch) -> None:
    calls: list[tuple[str | None, dict[str, Any], Settings]] = []
    enqueued: list[tuple[str | None, dict[str, Any], Settings]] = []

    async def _stub_process_ticket(
        delivery_id: str | None, payload: dict[str, Any], settings: Settings
    ) -> None:
        calls.append((delivery_id, payload, settings))

    async def _stub_enqueue_ticket_job(
        *, delivery_id: str | None, payload: dict[str, Any], settings: Settings
    ) -> str:
        enqueued.append((delivery_id, payload, settings))
        return "1-0"

    app = create_app(
        _test_settings(
            str(tmp_path),
            overrides={"workflow": {"execution_backend": "redis_queue", "redis_url": "redis://localhost/0"}},
        )
    )
    import zammad_pdf_archiver.app.routes.ingest as ingest_route

    monkeypatch.setattr(ingest_route, "process_ticket", _stub_process_ticket)
    monkeypatch.setattr(ingest_route, "enqueue_ticket_job", _stub_enqueue_ticket_job)
    client = TestClient(app)

    response = client.post(
        "/ingest",
        json={"ticket": {"id": 123}},
        headers={"X-Zammad-Delivery": "delivery-redis-1"},
    )
    assert response.status_code == 202
    assert response.json() == {"status": "accepted", "ticket_id": 123}
    assert len(enqueued) == 1
    assert enqueued[0][0] == "delivery-redis-1"
    assert calls == []


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
    response = client.post("/ingest/batch", json=payloads)
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
    response = client.post("/ingest/batch", json=payloads)
    assert response.status_code == 202
    assert response.json() == {"status": "accepted", "count": 100}
    assert len(calls) == 100


def test_jobs_queue_stats_endpoint_available(tmp_path) -> None:
    app = create_app(_test_settings_with_admin(str(tmp_path)))
    client = TestClient(app)

    response = client.get(
        "/jobs/queue/stats",
        headers={"Authorization": "Bearer test-admin-token"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["execution_backend"] == "inprocess"
    assert body["queue_enabled"] is False
