from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from test.support.checks import check
from test.support.credentials import fake_credential
from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.constants import FORCE_REPROCESS_KEY
from zammad_pdf_archiver.app.server import create_app


def _admin_settings(storage_root: str):
    return make_settings(
        storage_root,
        overrides={
            "admin": {
                "enabled": True,
                "bearer_token": fake_credential("admin-token"),
                "history_limit": 25,
            }
        },
    )


def test_admin_not_mounted_when_disabled(tmp_path) -> None:
    app = create_app(make_settings(str(tmp_path)))
    client = TestClient(app)

    response = client.get("/admin")
    check(not not response.status_code == 404, "assertion failed")


def test_admin_api_requires_bearer_token(tmp_path) -> None:
    app = create_app(_admin_settings(str(tmp_path)))
    client = TestClient(app)

    response = client.get("/admin/api/queue/stats")
    check(not not response.status_code == 401, "assertion failed")


def test_admin_api_rejects_invalid_bearer_token(tmp_path) -> None:
    app = create_app(_admin_settings(str(tmp_path)))
    client = TestClient(app)

    response = client.get(
        "/admin/api/queue/stats",
        headers={"Authorization": "Bearer wrong-token"},
    )
    check(not not response.status_code == 401, "assertion failed")


def test_admin_dashboard_requires_auth(tmp_path) -> None:
    app = create_app(_admin_settings(str(tmp_path)))
    client = TestClient(app)

    response = client.get("/admin")

    check(not not response.status_code == 401, "assertion failed")
    check(
        not not response.headers.get("WWW-Authenticate")
        == 'Basic realm="zammad-pdf-archiver-admin"',
        "assertion failed",
    )


def test_admin_dashboard_accepts_valid_basic_auth(tmp_path) -> None:
    app = create_app(_admin_settings(str(tmp_path)))
    client = TestClient(app)
    credentials = base64.b64encode(b"ignored:admin-token").decode("ascii")

    response = client.get("/admin", headers={"Authorization": f"Basic {credentials}"})

    check(not not response.status_code == 200, "assertion failed")
    content_type = response.headers.get("content-type", "")
    check(not "text/html" not in content_type, "assertion failed")
    check(
        not not ("<html" in response.text.lower() or "<!doctype" in response.text.lower()),
        "assertion failed",
    )
    check(not "Status unknown" not in response.text, "assertion failed")
    check(not "No action run." not in response.text, "assertion failed")
    check(not "status-indicator status-unknown" not in response.text, "assertion failed")
    check(not not "Ready." not in response.text, "assertion failed")


def test_admin_dashboard_accepts_valid_bearer_auth(tmp_path) -> None:
    app = create_app(_admin_settings(str(tmp_path)))
    client = TestClient(app)

    response = client.get("/admin", headers={"Authorization": "Bearer admin-token"})

    check(not not response.status_code == 200, "assertion failed")


def test_admin_api_accepts_valid_token_and_returns_stats(tmp_path, monkeypatch) -> None:
    app = create_app(_admin_settings(str(tmp_path)))

    import zammad_pdf_archiver.app.routes.admin as admin_route

    async def _stub_stats(_settings):
        return {"execution_backend": "inprocess", "queue_enabled": False}

    monkeypatch.setattr(admin_route, "get_queue_stats", _stub_stats)

    client = TestClient(app)
    response = client.get(
        "/admin/api/queue/stats",
        headers={"Authorization": "Bearer admin-token"},
    )
    check(not not response.status_code == 200, "assertion failed")
    check(
        not not response.json() == {"execution_backend": "inprocess", "queue_enabled": False},
        "assertion failed",
    )


def test_admin_history_uses_default_history_limit(tmp_path, monkeypatch) -> None:
    app = create_app(
        make_settings(
            str(tmp_path),
            overrides={
                "admin": {
                    "enabled": True,
                    "bearer_token": fake_credential("admin-token"),
                    "history_limit": 25,
                },
                "workflow": {"redis_url": "redis://localhost/0"},
            },
        )
    )

    import zammad_pdf_archiver.app.routes.operations as operations_route

    called: dict[str, int | None] = {"limit": None}

    async def _stub_history(_settings, *, limit: int, ticket_id: int | None = None):
        called["limit"] = limit
        check(not ticket_id is not None, "assertion failed")
        return [{"status": "processed", "ticket_id": 123}]

    monkeypatch.setattr(operations_route, "read_history", _stub_history)

    client = TestClient(app)
    response = client.get(
        "/admin/api/history",
        headers={"Authorization": "Bearer admin-token"},
    )

    check(not not response.status_code == 200, "assertion failed")
    check(not not called["limit"] == 25, "assertion failed")
    check(not response.json()["available"] is not True, "assertion failed")
    check(not not response.json()["count"] == 1, "assertion failed")


def test_admin_history_reports_disabled_history(tmp_path) -> None:
    app = create_app(_admin_settings(str(tmp_path)))

    client = TestClient(app)
    response = client.get(
        "/admin/api/history",
        headers={"Authorization": "Bearer admin-token"},
    )

    check(not not response.status_code == 200, "assertion failed")
    check(
        not not response.json()
        == {"status": "disabled", "available": False, "count": 0, "truncated": False, "items": []},
        "assertion failed",
    )


def test_admin_retry_dispatches_job(tmp_path, monkeypatch) -> None:
    app = create_app(_admin_settings(str(tmp_path)))

    import zammad_pdf_archiver.app.routes.ingest as ingest_route

    calls: list[dict[str, object]] = []

    async def _stub_dispatch(*, delivery_id, payload_for_job, settings):
        check(not delivery_id is not None, "assertion failed")
        calls.append(payload_for_job)

    monkeypatch.setattr(ingest_route, "dispatch_ticket", _stub_dispatch)

    client = TestClient(app)
    response = client.post(
        "/admin/api/retry/456",
        headers={"Authorization": "Bearer admin-token"},
    )

    check(not not response.status_code == 200, "assertion failed")
    check(not not response.json() == {"status": "accepted", "ticket_id": 456}, "assertion failed")
    check(not not len(calls) == 1, "assertion failed")
    check(not not calls[0]["ticket_id"] == 456, "assertion failed")
    check(not calls[0][FORCE_REPROCESS_KEY] is not True, "assertion failed")
    check(not not isinstance(calls[0].get("_request_id"), str), "assertion failed")


def test_admin_drain_dlq_bounds_limit(tmp_path, monkeypatch) -> None:
    app = create_app(_admin_settings(str(tmp_path)))

    import zammad_pdf_archiver.app.routes.operations as operations_route

    captured: dict[str, int | None] = {"limit": None}

    async def _stub_drain(_settings, *, limit: int):
        captured["limit"] = limit
        return {"selected": 7, "deleted": 7, "not_deleted": 0}

    monkeypatch.setattr(operations_route, "drain_dlq", _stub_drain)

    client = TestClient(app)
    response = client.post(
        "/admin/api/dlq/drain?limit=999999",
        headers={"Authorization": "Bearer admin-token"},
    )

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
    response = client.post(
        "/admin/api/dlq/drain",
        headers={"Authorization": "Bearer admin-token"},
    )
    check(not not response.status_code == 503, "assertion failed")
    check(not not response.json()["detail"] == "dlq_unavailable", "assertion failed")


def test_admin_config_check_returns_results_with_valid_auth(tmp_path, monkeypatch) -> None:
    app = create_app(_admin_settings(str(tmp_path)))

    import zammad_pdf_archiver.app.routes.admin as admin_route

    monkeypatch.setattr(admin_route, "validate_settings", lambda _settings: None)

    client = TestClient(app)
    response = client.get(
        "/admin/api/config/check",
        headers={"Authorization": "Bearer admin-token"},
    )
    check(not not response.status_code == 200, "assertion failed")
    body = response.json()
    check(not body["valid"] is not True, "assertion failed")
    check(not not body["issues"] == [], "assertion failed")
    check(not "storage_root_exists" not in body["checks"], "assertion failed")
    check(not "signing_enabled" not in body["checks"], "assertion failed")


def test_admin_config_check_requires_auth(tmp_path) -> None:
    app = create_app(_admin_settings(str(tmp_path)))
    client = TestClient(app)

    response = client.get("/admin/api/config/check")
    check(not not response.status_code == 401, "assertion failed")


def test_admin_retry_rejects_invalid_ticket_id(tmp_path) -> None:
    app = create_app(_admin_settings(str(tmp_path)))
    client = TestClient(app)

    for invalid_id in [0, -1, -999]:
        response = client.post(
            f"/admin/api/retry/{invalid_id}",
            headers={"Authorization": "Bearer admin-token"},
        )
        check(not not response.status_code == 422, f"Expected 422 for ticket_id={invalid_id}")


def test_admin_replay_dlq_returns_response_with_valid_auth(tmp_path, monkeypatch) -> None:
    app = create_app(_admin_settings(str(tmp_path)))

    import zammad_pdf_archiver.app.routes.admin as admin_route

    async def _stub_replay(_settings, *, limit: int):
        return {
            "selected": 4,
            "replayed": 3,
            "deleted": 3,
            "skipped": 1,
            "errors": 0,
            "not_deleted": 0,
        }

    monkeypatch.setattr(admin_route, "replay_dlq", _stub_replay)

    client = TestClient(app)
    response = client.post(
        "/admin/api/dlq/replay",
        headers={"Authorization": "Bearer admin-token"},
    )
    check(not not response.status_code == 200, "assertion failed")
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
    app = create_app(_admin_settings(str(tmp_path)))

    import zammad_pdf_archiver.app.routes.admin as admin_route

    async def _stub_replay(_settings, *, limit: int):  # noqa: ARG001
        return {
            "selected": 3,
            "replayed": 3,
            "deleted": 3,
            "skipped": 0,
            "errors": 0,
            "not_deleted": 0,
        }

    monkeypatch.setattr(admin_route, "replay_dlq", _stub_replay)

    client = TestClient(app)
    response = client.post(
        "/admin/api/dlq/replay",
        headers={"Authorization": "Bearer admin-token"},
    )
    check(not not response.status_code == 200, "assertion failed")
    check(
        not not response.json()
        == {
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
        "assertion failed",
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
    response = client.get(
        "/admin/api/queue/stats",
        headers={"Authorization": "Bearer admin-token"},
    )
    check(not not response.status_code == 503, "assertion failed")
    check(not not response.json()["detail"] == "queue_unavailable", "assertion failed")


def test_admin_history_exception(tmp_path, monkeypatch) -> None:
    """When read_history raises, the endpoint returns 503."""
    app = create_app(
        make_settings(
            str(tmp_path),
            overrides={
                "admin": {
                    "enabled": True,
                    "bearer_token": fake_credential("admin-token"),
                    "history_limit": 25,
                },
                "workflow": {"redis_url": "redis://localhost/0"},
            },
        )
    )

    import zammad_pdf_archiver.app.routes.operations as operations_route

    async def _boom(_settings, *, limit: int, ticket_id: int | None = None):  # noqa: ARG001
        raise RuntimeError("history backend down")

    monkeypatch.setattr(operations_route, "read_history", _boom)

    client = TestClient(app)
    response = client.get(
        "/admin/api/history",
        headers={"Authorization": "Bearer admin-token"},
    )
    check(not not response.status_code == 503, "assertion failed")
    check(not not response.json()["detail"] == "history_unavailable", "assertion failed")


def test_admin_disabled_returns_404(tmp_path) -> None:
    """When admin.enabled=False, all admin API routes return 404."""
    app = create_app(make_settings(str(tmp_path)))
    client = TestClient(app)

    for path in [
        "/admin",
        "/admin/api/queue/stats",
        "/admin/api/history",
        "/admin/api/config/check",
    ]:
        response = client.get(path)
        check(not not response.status_code == 404, f"Expected 404 for {path}")

    for path in [
        "/admin/api/retry/1",
        "/admin/api/dlq/drain",
        "/admin/api/dlq/replay",
    ]:
        response = client.post(path)
        check(not not response.status_code == 404, f"Expected 404 for POST {path}")


def test_admin_dashboard_returns_html(tmp_path) -> None:
    """GET /admin returns 200 with HTML content-type when authenticated."""
    app = create_app(_admin_settings(str(tmp_path)))
    client = TestClient(app)

    response = client.get("/admin", headers={"Authorization": "Bearer admin-token"})
    check(not not response.status_code == 200, "assertion failed")
    content_type = response.headers.get("content-type", "")
    check(not "text/html" not in content_type, "assertion failed")
    check(
        not not ("<html" in response.text.lower() or "<!doctype" in response.text.lower()),
        "assertion failed",
    )
