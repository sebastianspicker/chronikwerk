from __future__ import annotations

from fastapi.testclient import TestClient

from test.support.admin_api_helpers import admin_auth_headers as _admin_auth_headers
from test.support.admin_api_helpers import admin_client as _admin_client
from test.support.admin_api_helpers import admin_redis_app as _admin_redis_app
from test.support.admin_api_helpers import admin_settings as _admin_settings
from test.support.admin_api_helpers import get_admin_history as _get_admin_history
from test.support.admin_api_helpers import post_admin_replay as _post_admin_replay
from test.support.admin_api_helpers import stub_admin_replay as _stub_admin_replay
from test.support.checks import check
from test.support.integration_helpers import (
    assert_disabled_history_response,
    assert_json_response,
    check_status_ok,
)
from zammad_pdf_archiver.app.server import create_app


def test_admin_api_accepts_valid_token_and_returns_stats(tmp_path, monkeypatch) -> None:
    app = create_app(_admin_settings(str(tmp_path)))

    import zammad_pdf_archiver.app.routes.admin as admin_route

    async def _stub_stats(_settings):
        return {"execution_backend": "inprocess", "queue_enabled": False}

    monkeypatch.setattr(admin_route, "get_queue_stats", _stub_stats)

    client = TestClient(app)
    response = client.get("/admin/api/queue/stats", headers=_admin_auth_headers())
    check(not not response.status_code == 200, "assertion failed")
    check(
        not not response.json() == {"execution_backend": "inprocess", "queue_enabled": False},
        "assertion failed",
    )


def test_admin_history_uses_default_history_limit(tmp_path, monkeypatch) -> None:
    app = _admin_redis_app(tmp_path)

    import zammad_pdf_archiver.app.routes.operations as operations_route

    called: dict[str, int | None] = {"limit": None}

    async def _stub_history(_settings, *, limit: int, ticket_id: int | None = None):
        called["limit"] = limit
        check(not ticket_id is not None, "assertion failed")
        return [{"status": "processed", "ticket_id": 123}]

    monkeypatch.setattr(operations_route, "read_history", _stub_history)

    response = _get_admin_history(TestClient(app))

    check(not not response.status_code == 200, "assertion failed")
    check(not not called["limit"] == 25, "assertion failed")
    check(not response.json()["available"] is not True, "assertion failed")
    check(not not response.json()["count"] == 1, "assertion failed")


def test_admin_history_reports_disabled_history(tmp_path) -> None:
    app = create_app(_admin_settings(str(tmp_path)))
    client = TestClient(app)

    response = client.get("/admin/api/history", headers=_admin_auth_headers())

    assert_disabled_history_response(response)


def test_admin_drain_dlq_bounds_limit(tmp_path, monkeypatch) -> None:
    app = create_app(_admin_settings(str(tmp_path)))

    import zammad_pdf_archiver.app.routes.operations as operations_route

    captured: dict[str, int | None] = {"limit": None}

    async def _stub_drain(_settings, *, limit: int):
        captured["limit"] = limit
        return {"selected": 7, "deleted": 7, "not_deleted": 0}

    monkeypatch.setattr(operations_route, "drain_dlq", _stub_drain)

    client = TestClient(app)
    response = client.post("/admin/api/dlq/drain?limit=999999", headers=_admin_auth_headers())

    check(not not response.status_code == 200, "assertion failed")
    check(not not captured["limit"] == 1000, "assertion failed")
    check(
        not not response.json()
        == {"status": "ok", "drained": 7, "selected": 7, "deleted": 7, "not_deleted": 0},
        "assertion failed",
    )


def test_admin_drain_dlq_returns_503_when_backend_unavailable(tmp_path, monkeypatch) -> None:
    app = create_app(_admin_settings(str(tmp_path)))

    import zammad_pdf_archiver.app.routes.operations as operations_route

    async def _boom(_settings, *, limit: int):  # noqa: ARG001
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(operations_route, "drain_dlq", _boom)

    client = TestClient(app)
    response = client.post("/admin/api/dlq/drain", headers=_admin_auth_headers())
    check(not not response.status_code == 503, "assertion failed")
    check(not not response.json()["detail"] == "dlq_unavailable", "assertion failed")


def test_admin_replay_dlq_returns_response_with_valid_auth(tmp_path, monkeypatch) -> None:
    _stub_admin_replay(monkeypatch, selected=4, replayed=3, skipped=1)
    response = _post_admin_replay(_admin_client(tmp_path))

    check_status_ok(response)
    body = response.json()
    check(not not body["status"] == "partial", "assertion failed")
    check(not not body["selected"] == 4, "assertion failed")
    check(not not body["replayed"] == 3, "assertion failed")
    check(not not body["deleted"] == 3, "assertion failed")
    check(not not body["skipped"] == 1, "assertion failed")
    check(not not body["errors"] == 0, "assertion failed")
    check(not not body["not_deleted"] == 0, "assertion failed")
    check(not body["idempotent"] is not False, "assertion failed")
    check(not not body["duplicate_risk"] == 0, "assertion failed")


def test_admin_replay_dlq_reports_ok_only_for_full_success(tmp_path, monkeypatch) -> None:
    _stub_admin_replay(monkeypatch, selected=3, replayed=3, skipped=0)
    response = _post_admin_replay(_admin_client(tmp_path))

    assert_json_response(
        response,
        {
            "status": "ok",
            "idempotent": False,
            "duplicate_risk": 0,
            "selected": 3,
            "replayed": 3,
            "deleted": 3,
            "skipped": 0,
            "errors": 0,
            "not_deleted": 0,
        },
    )


def test_admin_replay_dlq_requires_auth(tmp_path) -> None:
    app = create_app(_admin_settings(str(tmp_path)))
    client = TestClient(app)

    response = client.post("/admin/api/dlq/replay")
    check(not not response.status_code == 401, "assertion failed")


def test_admin_queue_stats_exception(tmp_path, monkeypatch) -> None:
    """When get_queue_stats raises, the endpoint returns 503."""
    app = create_app(_admin_settings(str(tmp_path)))

    import zammad_pdf_archiver.app.routes.admin as admin_route

    async def _boom(_settings):
        raise RuntimeError("redis exploded")

    monkeypatch.setattr(admin_route, "get_queue_stats", _boom)

    client = TestClient(app)
    response = client.get("/admin/api/queue/stats", headers=_admin_auth_headers())
    check(not not response.status_code == 503, "assertion failed")
    check(not not response.json()["detail"] == "queue_unavailable", "assertion failed")


def test_admin_history_exception(tmp_path, monkeypatch) -> None:
    """When read_history raises, the endpoint returns 503."""
    app = _admin_redis_app(tmp_path)

    import zammad_pdf_archiver.app.routes.operations as operations_route

    async def _boom(_settings, *, limit: int, ticket_id: int | None = None):  # noqa: ARG001
        raise RuntimeError("history backend down")

    monkeypatch.setattr(operations_route, "read_history", _boom)

    response = _get_admin_history(TestClient(app))
    check(not not response.status_code == 503, "assertion failed")
    check(not not response.json()["detail"] == "history_unavailable", "assertion failed")
