from __future__ import annotations

from fastapi.testclient import TestClient

from test.support.admin_api_helpers import admin_settings as _admin_settings
from test.support.admin_api_helpers import assert_html_response as _assert_html_response
from test.support.admin_api_helpers import basic_admin_headers as _basic_admin_headers
from test.support.checks import check
from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.constants import FORCE_REPROCESS_KEY
from zammad_pdf_archiver.app.server import create_app


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

    response = client.get("/admin", headers=_basic_admin_headers())

    _assert_html_response(response)
    check(not "Status unknown" not in response.text, "assertion failed")
    check(not "No action run." not in response.text, "assertion failed")
    check(not "status-indicator status-unknown" not in response.text, "assertion failed")
    check(not not "Ready." not in response.text, "assertion failed")


def test_admin_dashboard_accepts_valid_bearer_auth(tmp_path) -> None:
    app = create_app(_admin_settings(str(tmp_path)))
    client = TestClient(app)

    response = client.get("/admin", headers={"Authorization": "Bearer admin-token"})

    check(not not response.status_code == 200, "assertion failed")


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
    _assert_html_response(response)
