from __future__ import annotations

import asyncio
from typing import Any

from fastapi.testclient import TestClient

import zammad_pdf_archiver.app.routes.ingest as ingest_route
import zammad_pdf_archiver.app.routes.jobs as jobs_route
from test.support.checks import check
from test.support.credentials import fake_credential
from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.constants import FORCE_REPROCESS_KEY
from zammad_pdf_archiver.app.jobs import ticket_stores
from zammad_pdf_archiver.app.server import create_app
from zammad_pdf_archiver.config.settings import Settings

ADMIN_HEADERS = {"Authorization": "Bearer test-admin-token"}
REDIS_WORKFLOW = {"workflow": {"execution_backend": "redis_queue", "redis_url": "redis://localhost/0"}}
REDIS_IDEMPOTENCY_WORKFLOW = {
    "workflow": {
        "idempotency_backend": "redis",
        "redis_url": "redis://localhost:6379/0",
    }
}


def _test_settings(
    storage_root: str,
    *,
    overrides: dict[str, Any] | None = None,
) -> Settings:
    return make_settings(storage_root, overrides=overrides)


def _capture_process_ticket(
    monkeypatch,
) -> list[tuple[str | None, dict[str, Any], Settings]]:
    calls: list[tuple[str | None, dict[str, Any], Settings]] = []

    async def _stub_process_ticket(
        delivery_id: str | None, payload: dict[str, Any], settings: Settings
    ) -> None:
        calls.append((delivery_id, payload, settings))

    monkeypatch.setattr(ingest_route, "process_ticket", _stub_process_ticket)
    return calls


def _client_with_captured_process_ticket(
    settings: Settings,
    monkeypatch,
) -> tuple[TestClient, list[tuple[str | None, dict[str, Any], Settings]]]:
    calls = _capture_process_ticket(monkeypatch)
    return TestClient(create_app(settings)), calls


def _get_admin(client: TestClient, path: str):
    return client.get(path, headers=ADMIN_HEADERS)


def _post_admin(client: TestClient, path: str):
    return client.post(path, headers=ADMIN_HEADERS)


def _test_settings_with_admin(storage_root: str, **extra_overrides: Any) -> Settings:
    overrides: dict[str, Any] = {
        "admin": {
            "enabled": True,
            "bearer_token": fake_credential("test-admin-token"),
        }
    }
    if extra_overrides:
        for key, val in extra_overrides.items():
            overrides[key] = val
    return make_settings(storage_root, overrides=overrides)


def test_retry_endpoint_accepts_ticket_id(tmp_path, monkeypatch) -> None:
    client, calls = _client_with_captured_process_ticket(
        _test_settings_with_admin(str(tmp_path)),
        monkeypatch,
    )

    response = _post_admin(client, "/retry/987")
    check(not not response.status_code == 202, "assertion failed")
    check(not not response.json() == {"status": "accepted", "ticket_id": 987}, "assertion failed")
    check(not not len(calls) == 1, "assertion failed")
    check(not calls[0][0] is not None, "assertion failed")
    check(not not calls[0][1]["ticket_id"] == 987, "assertion failed")
    check(not calls[0][1][FORCE_REPROCESS_KEY] is not True, "assertion failed")


def test_retry_requires_auth(tmp_path) -> None:
    """POST /retry/{ticket_id} without Authorization header returns 401."""
    app = create_app(_test_settings_with_admin(str(tmp_path)))
    client = TestClient(app)

    response = client.post("/retry/123")
    check(not not response.status_code == 401, "assertion failed")
    check(not not response.json()["detail"] == "unauthorized", "assertion failed")


def test_retry_with_valid_token(tmp_path, monkeypatch) -> None:
    """POST /retry/{ticket_id} with valid bearer token returns 202."""
    client, calls = _client_with_captured_process_ticket(
        _test_settings_with_admin(str(tmp_path)),
        monkeypatch,
    )

    response = _post_admin(client, "/retry/123")
    check(not not response.status_code == 202, "assertion failed")
    check(not not response.json() == {"status": "accepted", "ticket_id": 123}, "assertion failed")
    check(not not len(calls) == 1, "assertion failed")
    check(not calls[0][1][FORCE_REPROCESS_KEY] is not True, "assertion failed")


def test_retry_with_invalid_token(tmp_path) -> None:
    """POST /retry/{ticket_id} with wrong bearer token returns 401."""
    app = create_app(_test_settings_with_admin(str(tmp_path)))
    client = TestClient(app)

    response = client.post(
        "/retry/123",
        headers={"Authorization": "Bearer wrong-token"},
    )
    check(not not response.status_code == 401, "assertion failed")
    check(not not response.json()["detail"] == "unauthorized", "assertion failed")


def test_jobs_endpoint_reports_in_flight_status(tmp_path) -> None:
    ticket_stores._reset_for_tests()
    settings = _test_settings_with_admin(str(tmp_path))
    app = create_app(settings)
    client = TestClient(app)

    acquired = asyncio.run(ticket_stores.try_acquire_ticket(settings, 404))
    check(not acquired is not True, "assertion failed")
    try:
        response = _get_admin(client, "/jobs/404")
        check(not not response.status_code == 200, "assertion failed")
        check(
            not not response.json()
            == {
                "ticket_id": 404,
                "in_flight": True,
                "process_local_in_flight": True,
                "distributed_in_flight": None,
                "shutting_down": False,
            },
            "assertion failed",
        )
    finally:
        asyncio.run(ticket_stores.release_ticket(settings, 404))
        ticket_stores._reset_for_tests()


def test_jobs_endpoint_reports_distributed_in_flight_status(tmp_path, monkeypatch) -> None:
    settings = _test_settings_with_admin(
        str(tmp_path),
        **REDIS_IDEMPOTENCY_WORKFLOW,
    )
    app = create_app(settings)
    client = TestClient(app)

    async def _distributed_in_flight(_settings: Settings, ticket_id: int) -> bool:
        check(not _settings is not settings, "assertion failed")
        check(not not ticket_id == 404, "assertion failed")
        return True

    monkeypatch.setattr(
        jobs_route,
        "is_ticket_distributed_in_flight",
        _distributed_in_flight,
    )

    response = _get_admin(client, "/jobs/404")
    check(not not response.status_code == 200, "assertion failed")
    check(
        not not response.json()
        == {
            "ticket_id": 404,
            "in_flight": True,
            "process_local_in_flight": False,
            "distributed_in_flight": True,
            "shutting_down": False,
        },
        "assertion failed",
    )


def test_jobs_endpoint_fails_closed_when_distributed_status_unavailable(
    tmp_path, monkeypatch
) -> None:
    app = create_app(
        _test_settings_with_admin(
            str(tmp_path),
            **REDIS_IDEMPOTENCY_WORKFLOW,
        )
    )
    client = TestClient(app)

    async def _distributed_unavailable(_settings: Settings, _ticket_id: int) -> bool:
        raise RuntimeError("redis down")

    monkeypatch.setattr(
        jobs_route,
        "is_ticket_distributed_in_flight",
        _distributed_unavailable,
    )

    response = _get_admin(client, "/jobs/404")
    check(not not response.status_code == 503, "assertion failed")
    check(not not response.json() == {"detail": "ticket_lock_unavailable"}, "assertion failed")


def test_ingest_uses_redis_queue_dispatch_when_enabled(tmp_path, monkeypatch) -> None:
    calls = _capture_process_ticket(monkeypatch)
    enqueued: list[tuple[str | None, dict[str, Any], Settings]] = []

    async def _stub_enqueue_ticket_job(
        *, delivery_id: str | None, payload: dict[str, Any], settings: Settings
    ) -> str:
        enqueued.append((delivery_id, payload, settings))
        return "1-0"

    app = create_app(
        _test_settings(
            str(tmp_path),
            overrides=REDIS_WORKFLOW,
        )
    )

    monkeypatch.setattr(ingest_route, "enqueue_ticket_job", _stub_enqueue_ticket_job)
    client = TestClient(app)

    response = client.post(
        "/ingest",
        json={"ticket": {"id": 123}},
        headers={"X-Zammad-Delivery": "delivery-redis-1"},
    )
    check(not not response.status_code == 202, "assertion failed")
    check(not not response.json() == {"status": "accepted", "ticket_id": 123}, "assertion failed")
    check(not not len(enqueued) == 1, "assertion failed")
    check(not not enqueued[0][0] == "delivery-redis-1", "assertion failed")
    check(not not calls == [], "assertion failed")


def test_batch_ingest_exceeds_max_size(tmp_path, monkeypatch) -> None:
    """POST /ingest/batch with 101 items returns 422 (batch too large)."""
    client, calls = _client_with_captured_process_ticket(
        _test_settings(str(tmp_path)),
        monkeypatch,
    )

    payloads = [{"ticket_id": i} for i in range(1, 102)]  # 101 items
    response = client.post("/ingest/batch", json=payloads)
    check(not not response.status_code == 422, "assertion failed")
    body = response.json()
    check(not not body["code"] == "batch_too_large", "assertion failed")
    check(not not calls == [], "assertion failed")


def test_batch_ingest_at_max_size(tmp_path, monkeypatch) -> None:
    """POST /ingest/batch with exactly 100 items is accepted (202)."""
    client, calls = _client_with_captured_process_ticket(
        _test_settings(str(tmp_path)),
        monkeypatch,
    )

    payloads = [{"ticket_id": i} for i in range(1, 101)]  # exactly 100 items
    response = client.post("/ingest/batch", json=payloads)
    check(not not response.status_code == 202, "assertion failed")
    check(not not response.json() == {"status": "accepted", "count": 100}, "assertion failed")
    check(not not len(calls) == 100, "assertion failed")


def test_batch_ingest_dispatch_failure_reports_partial_acceptance(tmp_path, monkeypatch) -> None:
    """A mid-batch dispatch failure must expose prior accepted side effects."""
    calls: list[tuple[str | None, dict[str, Any], Settings]] = []

    async def _stub_dispatch_ticket(
        *,
        delivery_id: str | None,
        payload_for_job: dict[str, Any],
        settings: Settings,
    ) -> None:
        calls.append((delivery_id, payload_for_job, settings))
        if len(calls) == 2:
            raise RuntimeError("redis unavailable")

    monkeypatch.setattr(ingest_route, "dispatch_ticket", _stub_dispatch_ticket)
    client = TestClient(create_app(_test_settings(str(tmp_path))))

    response = client.post(
        "/ingest/batch",
        json=[{"ticket_id": 101}, {"ticket_id": 202}, {"ticket_id": 303}],
        headers={"X-Zammad-Delivery": "delivery-partial"},
    )

    check(not not response.status_code == 503, "assertion failed")
    check(
        not not response.json()
        == {
            "status": "partial_failure",
            "code": "batch_dispatch_failed",
            "accepted": 1,
            "failed_index": 1,
            "failed_ticket_id": 202,
        },
        "assertion failed",
    )
    check(
        not not [call[0] for call in calls] == ["delivery-partial:0", "delivery-partial:1"],
        "assertion failed",
    )


def test_jobs_queue_stats_endpoint_available(tmp_path) -> None:
    app = create_app(_test_settings_with_admin(str(tmp_path)))
    client = TestClient(app)

    response = _get_admin(client, "/jobs/queue/stats")
    check(not not response.status_code == 200, "assertion failed")
    body = response.json()
    check(not not body["execution_backend"] == "inprocess", "assertion failed")
    check(not body["queue_enabled"] is not False, "assertion failed")
