from __future__ import annotations

from fastapi.testclient import TestClient

import zammad_pdf_archiver.app.routes.operations as operations_route
from test.support.checks import check
from test.support.credentials import fake_credential
from test.support.integration_helpers import assert_disabled_history_response
from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.server import create_app

OPS_HEADERS = {"Authorization": "Bearer ops-token"}


def _ops_auth_settings(storage_root: str):
    return make_settings(
        storage_root,
        overrides={"admin": {"bearer_token": fake_credential("ops-token")}},
    )


def _ops_redis_app(tmp_path):
    return create_app(
        make_settings(
            str(tmp_path),
            overrides={
                "admin": {"bearer_token": fake_credential("ops-token")},
                "workflow": {"redis_url": "redis://localhost/0"},
            },
        )
    )


def test_jobs_history_endpoint_returns_items(tmp_path, monkeypatch) -> None:
    app = _ops_redis_app(tmp_path)

    async def _stub_history(_settings, *, limit: int, ticket_id: int | None = None):
        check(not not limit == 50, "assertion failed")
        check(not not ticket_id == 123, "assertion failed")
        return [{"status": "processed", "ticket_id": 123}]

    monkeypatch.setattr(operations_route, "read_history", _stub_history)

    client = TestClient(app)
    response = client.get(
        "/jobs/history?limit=50&ticket_id=123",
        headers=OPS_HEADERS,
    )
    check(not not response.status_code == 200, "assertion failed")
    check(
        not not response.json()
        == {
            "status": "ok",
            "available": True,
            "count": 1,
            "truncated": False,
            "items": [{"status": "processed", "ticket_id": 123}],
        },
        "assertion failed",
    )


def test_jobs_history_endpoint_reports_disabled_history(tmp_path) -> None:
    app = create_app(_ops_auth_settings(str(tmp_path)))

    client = TestClient(app)
    response = client.get(
        "/jobs/history",
        headers=OPS_HEADERS,
    )

    assert_disabled_history_response(response)


def test_jobs_history_endpoint_distinguishes_empty_enabled_history(tmp_path, monkeypatch) -> None:
    app = _ops_redis_app(tmp_path)

    async def _stub_history(_settings, *, limit: int, ticket_id: int | None = None):  # noqa: ARG001
        return []

    monkeypatch.setattr(operations_route, "read_history", _stub_history)

    client = TestClient(app)
    response = client.get(
        "/jobs/history",
        headers=OPS_HEADERS,
    )

    check(not not response.status_code == 200, "assertion failed")
    check(
        not not response.json()
        == {"status": "ok", "available": True, "count": 0, "truncated": False, "items": []},
        "assertion failed",
    )


def test_jobs_history_endpoint_reports_truncated_when_limit_reached(
    tmp_path,
    monkeypatch,
) -> None:
    app = _ops_redis_app(tmp_path)

    async def _stub_history(_settings, *, limit: int, ticket_id: int | None = None):  # noqa: ARG001
        check(not not limit == 2, "assertion failed")
        return [
            {"status": "processed", "ticket_id": 123},
            {"status": "processed", "ticket_id": 124},
        ]

    monkeypatch.setattr(operations_route, "read_history", _stub_history)

    client = TestClient(app)
    response = client.get(
        "/jobs/history?limit=2",
        headers=OPS_HEADERS,
    )

    check(not not response.status_code == 200, "assertion failed")
    check(not not response.json()["count"] == 2, "assertion failed")
    check(not response.json()["truncated"] is not True, "assertion failed")


def test_jobs_history_endpoint_returns_503_on_backend_error(tmp_path, monkeypatch) -> None:
    app = _ops_redis_app(tmp_path)

    async def _boom(_settings, *, limit: int, ticket_id: int | None = None):  # noqa: ARG001
        raise RuntimeError("history backend down")

    monkeypatch.setattr(operations_route, "read_history", _boom)

    client = TestClient(app)
    response = client.get(
        "/jobs/history",
        headers=OPS_HEADERS,
    )

    check(not not response.status_code == 503, "assertion failed")
    check(not not response.json()["detail"] == "history_unavailable", "assertion failed")


def test_jobs_dlq_drain_endpoint_bounds_limit(tmp_path, monkeypatch) -> None:
    app = create_app(_ops_auth_settings(str(tmp_path)))

    captured: dict[str, int | None] = {"limit": None}

    async def _stub_drain(_settings, *, limit: int):
        captured["limit"] = limit
        return {"selected": 4, "deleted": 4, "not_deleted": 0}

    monkeypatch.setattr(operations_route, "drain_dlq", _stub_drain)

    client = TestClient(app)
    response = client.post(
        "/jobs/queue/dlq/drain?limit=2000",
        headers=OPS_HEADERS,
    )
    check(not not response.status_code == 200, "assertion failed")
    check(not not captured["limit"] == 1000, "assertion failed")
    check(
        not not response.json()
        == {"status": "ok", "drained": 4, "selected": 4, "deleted": 4, "not_deleted": 0},
        "assertion failed",
    )


def test_jobs_dlq_drain_endpoint_reports_partial_delete(tmp_path, monkeypatch) -> None:
    app = create_app(_ops_auth_settings(str(tmp_path)))

    async def _stub_drain(_settings, *, limit: int):  # noqa: ARG001
        return {"selected": 3, "deleted": 2, "not_deleted": 1}

    monkeypatch.setattr(operations_route, "drain_dlq", _stub_drain)

    client = TestClient(app)
    response = client.post(
        "/jobs/queue/dlq/drain?limit=3",
        headers=OPS_HEADERS,
    )
    check(not not response.status_code == 200, "assertion failed")
    check(
        not not response.json()
        == {"status": "partial", "drained": 2, "selected": 3, "deleted": 2, "not_deleted": 1},
        "assertion failed",
    )


def test_jobs_history_requires_bearer_token(tmp_path) -> None:
    app = create_app(_ops_auth_settings(str(tmp_path)))
    client = TestClient(app)

    response = client.get("/jobs/history")
    check(not not response.status_code == 401, "assertion failed")


def test_jobs_dlq_drain_requires_bearer_token(tmp_path) -> None:
    app = create_app(_ops_auth_settings(str(tmp_path)))
    client = TestClient(app)

    response = client.post("/jobs/queue/dlq/drain")
    check(not not response.status_code == 401, "assertion failed")


def test_jobs_history_requires_configured_ops_token(tmp_path) -> None:
    app = create_app(make_settings(str(tmp_path)))
    client = TestClient(app)

    response = client.get("/jobs/history")
    check(not not response.status_code == 503, "assertion failed")
    check(not not response.json()["detail"] == "admin_token_not_configured", "assertion failed")


def test_jobs_dlq_drain_returns_503_on_backend_error(tmp_path, monkeypatch) -> None:
    app = create_app(_ops_auth_settings(str(tmp_path)))

    async def _boom(_settings, *, limit: int):  # noqa: ARG001
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(operations_route, "drain_dlq", _boom)

    client = TestClient(app)
    response = client.post(
        "/jobs/queue/dlq/drain",
        headers=OPS_HEADERS,
    )
    check(not not response.status_code == 503, "assertion failed")
    check(not not response.json()["detail"] == "dlq_unavailable", "assertion failed")
