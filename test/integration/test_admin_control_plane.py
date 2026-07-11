from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.admin import routes as admin_routes
from zammad_pdf_archiver.app.jobs.history import record_history_event, reset_for_tests
from zammad_pdf_archiver.app.server import create_app

_TOKEN = "admin-access-token-that-is-at-least-32-characters"


def _settings(tmp_path: Path, *, enabled: bool = True):
    return make_settings(
        str(tmp_path / "archive"),
        secret="webhook-secret",
        overrides={
            "admin": {
                "enabled": enabled,
                "access_token": _TOKEN,
                "state_dir": str(tmp_path / "admin-state"),
                "cookie_secure": False,
            }
        },
    )


def _signed_in_client(tmp_path: Path) -> tuple[TestClient, str]:
    client = TestClient(create_app(_settings(tmp_path)))
    response = client.post(
        "/admin/api/v1/session",
        json={"access_token": _TOKEN, "locale": "en_GB"},
    )
    assert response.status_code == 204
    page = client.get("/admin")
    assert page.status_code == 200
    match = re.search(r'<meta name="csrf-token" content="([^"]+)">', page.text)
    assert match is not None
    return client, match.group(1)


def test_admin_is_absent_when_disabled(tmp_path: Path) -> None:
    client = TestClient(create_app(_settings(tmp_path, enabled=False)))

    assert client.get("/admin").status_code == 404
    assert client.get("/admin/static/admin.css").status_code == 404
    assert client.post("/admin/api/v1/session", json={"access_token": _TOKEN}).status_code == 404


def test_login_session_csrf_headers_and_logout(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    client = TestClient(app)

    invalid = client.post("/admin/api/v1/session", json={"access_token": "wrong"})
    assert invalid.status_code == 401
    assert invalid.json()["code"] == "invalid_credentials"

    login = client.post("/admin/api/v1/session", json={"access_token": _TOKEN})
    assert login.status_code == 204
    cookie = login.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert "Path=/admin" in cookie

    page = client.get("/admin")
    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert page.headers["x-content-type-options"] == "nosniff"
    assert '<html lang="de-DE">' in page.text
    session = app.state.admin_sessions.get(client.cookies.get("zpa_admin_session"))
    assert session is not None

    missing_csrf = client.post("/admin/api/v1/status/storage-check")
    assert missing_csrf.status_code == 403
    status = client.get("/admin/api/v1/status")
    assert status.status_code == 200
    assert status.json()["history"] == {"volatile": True, "limit": 5000}

    logout = client.delete(
        "/admin/api/v1/session",
        headers={"X-CSRF-Token": session.csrf_token},
    )
    assert logout.status_code == 204
    assert client.get("/admin/api/v1/status").status_code == 401


def test_jobs_cursor_filter_and_safe_retry(tmp_path: Path, monkeypatch) -> None:
    reset_for_tests()
    client, csrf = _signed_in_client(tmp_path)
    record_history_event("failed", 12, "permanent", "bad input", request_id="r1")
    record_history_event("processed", 13, request_id="r2")
    record_history_event("failed", 12, "transient", "timeout", request_id="r3")

    response = client.get("/admin/api/v1/jobs?limit=1&ticket_id=12&status=failed")
    assert response.status_code == 200
    payload = response.json()
    assert [item["request_id"] for item in payload["items"]] == ["r3"]
    assert payload["next_cursor"] is not None
    assert payload["volatile"] is True

    monkeypatch.setattr(admin_routes, "schedule_retry", lambda *_args, **_kwargs: True)
    missing_ack = client.post(
        "/admin/api/v1/jobs/12/retry",
        json={"acknowledge_overwrite": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert missing_ack.status_code == 422
    accepted = client.post(
        "/admin/api/v1/jobs/12/retry",
        json={"acknowledge_overwrite": True},
        headers={"X-CSRF-Token": csrf},
    )
    assert accepted.status_code == 202
    assert accepted.json()["status"] == "accepted"


def test_config_redaction_validation_staging_and_conflict(tmp_path: Path) -> None:
    client, csrf = _signed_in_client(tmp_path)
    config = client.get("/admin/api/v1/config")
    assert config.status_code == 200
    body = config.json()
    assert body["secret_presence"]["zammad.api_token"] is True
    assert _TOKEN not in config.text
    revision = body["revision"]

    invalid = client.post(
        "/admin/api/v1/config/validate",
        json={"values": {"zammad.api_token": "leak"}},
        headers={"X-CSRF-Token": csrf},
    )
    assert invalid.status_code == 422
    assert "leak" not in invalid.text

    validated = client.post(
        "/admin/api/v1/config/validate",
        json={"values": {"pdf.locale": "en_GB", "pdf.max_articles": 100}},
        headers={"X-CSRF-Token": csrf},
    )
    assert validated.status_code == 200
    overlay = validated.json()["overlay"]
    assert overlay["pdf"]["locale"] == "en-GB"

    staged = client.put(
        "/admin/api/v1/config/staged",
        json={"overlay": overlay},
        headers={"X-CSRF-Token": csrf, "If-Match": revision},
    )
    assert staged.status_code == 200
    assert staged.json()["restart_required"] is True
    status = client.get("/admin/api/v1/status").json()
    assert status["config"]["staged_revision"] == staged.json()["revision"]

    conflict = client.put(
        "/admin/api/v1/config/staged",
        json={"overlay": overlay},
        headers={"X-CSRF-Token": csrf, "If-Match": revision},
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "config_revision_conflict"
