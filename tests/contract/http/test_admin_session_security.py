"""Pin administration authentication, CSRF, cookie, header, and session-expiry behavior."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

from chronikwerk.web.admin.auth import AdminSessionStore
from chronikwerk.web.app import create_app
from tests.support.settings_factory import make_settings

_TOKEN = "admin-token-0123456789abcdef0123456789"
_EXPECTED_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
    "connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; "
    "form-action 'self'"
)


def _client(tmp_path, *, enabled: bool = True, root_path: str = "") -> TestClient:
    """Create an HTTPS test client with administration enabled or disabled."""
    (tmp_path / "archive").mkdir()
    settings = make_settings(
        str(tmp_path / "archive"),
        overrides={
            "admin": {
                "enabled": enabled,
                "access_token": _TOKEN,
                "state_dir": str(tmp_path / "admin-state"),
            }
        },
    )
    return TestClient(
        create_app(settings),
        base_url="https://testserver",
        root_path=root_path,
    )


def _login(client: TestClient, **form: str):
    """Submit the login form without following the redirect."""
    data = {"access_token": _TOKEN, **form}
    return client.post("/admin/login", data=data, follow_redirects=False)


def _csrf(client: TestClient) -> str:
    """Read the anti-forgery token of the authenticated session."""
    page = client.get("/admin/configuration")
    match = re.search(r'<meta name="csrf-token" content="([^"]+)">', page.text)
    assert match is not None
    return match.group(1)


def test_wrong_access_token_redirects_back_without_session(tmp_path) -> None:
    client = _client(tmp_path)

    response = _login(client, access_token="wrong")

    assert response.status_code == 303
    location = urlsplit(response.headers["location"])
    assert location.path == "/admin/login"
    assert parse_qs(location.query)["error"] == ["true"]
    assert "set-cookie" not in response.headers
    assert client.get("/admin/api/v1/status").status_code == 401


def test_wrong_access_token_via_session_api_is_invalid_credentials(tmp_path) -> None:
    client = _client(tmp_path)

    response = client.post("/admin/api/v1/session", json={"access_token": "wrong"})

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"
    assert set(response.json()) == {"code", "message", "request_id"}


def test_status_api_requires_session_cookie(tmp_path) -> None:
    client = _client(tmp_path)

    response = client.get("/admin/api/v1/status")

    assert response.status_code == 401
    assert response.json()["code"] == "admin_session_required"


def test_status_api_with_session_reports_service(tmp_path) -> None:
    client = _client(tmp_path)
    _login(client)

    response = client.get("/admin/api/v1/status")

    assert response.status_code == 200
    assert response.json()["service"] == "chronikwerk"


def test_state_changing_api_rejects_missing_csrf_header(tmp_path) -> None:
    client = _client(tmp_path)
    _login(client)

    response = client.post("/admin/api/v1/status/storage-check")

    assert response.status_code == 403
    assert response.json()["code"] == "csrf_invalid"


def test_state_changing_api_rejects_wrong_csrf_header(tmp_path) -> None:
    client = _client(tmp_path)
    _login(client)

    response = client.post("/admin/api/v1/status/storage-check", headers={"X-CSRF-Token": "bad"})

    assert response.status_code == 403
    assert response.json()["code"] == "csrf_invalid"


def test_state_changing_api_accepts_matching_csrf_header(tmp_path) -> None:
    client = _client(tmp_path)
    _login(client)
    token = _csrf(client)

    response = client.post("/admin/api/v1/status/storage-check", headers={"X-CSRF-Token": token})

    assert response.status_code == 200
    assert response.json()["storage"] == {"writable": True}


def test_admin_paths_are_not_found_when_administration_is_disabled(tmp_path) -> None:
    client = _client(tmp_path, enabled=False)

    assert client.get("/admin/login").status_code == 404
    assert client.get("/admin/api/v1/status").status_code == 404
    assert client.post("/admin/login", data={"access_token": _TOKEN}).status_code == 404


def test_session_cookie_is_http_only_strict_and_scoped_to_admin(tmp_path) -> None:
    client = _client(tmp_path)

    cookie = _login(client).headers["set-cookie"]
    attributes = {part.strip().lower() for part in cookie.split(";")[1:]}

    assert cookie.startswith("zpa_admin_session=")
    assert {"httponly", "secure", "path=/admin", "samesite=strict"} <= attributes


def test_admin_responses_carry_security_headers(tmp_path) -> None:
    client = _client(tmp_path)

    response = client.get("/admin/login")

    assert response.headers["content-security-policy"] == _EXPECTED_CSP
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-frame-options"] == "DENY"


def test_root_path_admin_responses_carry_security_headers(tmp_path) -> None:
    client = _client(tmp_path, root_path="/archive")

    response = client.get("/archive/admin/login")

    assert response.headers["content-security-policy"] == _EXPECTED_CSP
    assert response.headers["cache-control"] == "no-store"


def test_security_headers_are_not_added_outside_admin(tmp_path) -> None:
    client = _client(tmp_path)

    assert "content-security-policy" not in client.get("/healthz").headers


def test_login_next_to_external_host_stays_inside_admin(tmp_path) -> None:
    client = _client(tmp_path)

    response = _login(client, next="//evil.example/phish")

    assert response.status_code == 303
    assert response.headers["location"] == "/admin"


def test_login_next_to_admin_path_is_honoured(tmp_path) -> None:
    client = _client(tmp_path)

    response = _login(client, next="/admin/configuration")

    assert response.headers["location"] == "/admin/configuration"


def test_logout_without_csrf_keeps_session_and_redirects_to_login(tmp_path) -> None:
    client = _client(tmp_path)
    _login(client)

    response = client.post("/admin/logout", data={}, follow_redirects=False)

    assert response.status_code == 303
    assert client.get("/admin/api/v1/status").status_code == 200


def test_logout_with_csrf_ends_session(tmp_path) -> None:
    client = _client(tmp_path)
    _login(client)
    token = _csrf(client)

    client.post("/admin/logout", data={"csrf_token": token}, follow_redirects=False)

    assert client.get("/admin/api/v1/status").status_code == 401


def _store() -> AdminSessionStore:
    """Build a store with a five-minute idle and ten-minute absolute lifetime."""
    return AdminSessionStore(idle_seconds=300, absolute_seconds=600)


def test_session_survives_prune_inside_idle_window() -> None:
    store = _store()
    session = store.create(locale="en-GB")

    store.prune(now=session.last_seen_at + 299)

    assert store.get(session.session_id) is session


def test_session_is_pruned_after_idle_expiry() -> None:
    store = _store()
    session = store.create(locale="en-GB")

    store.prune(now=session.last_seen_at + 301)

    assert store.get(session.session_id) is None


def test_active_session_is_pruned_after_absolute_expiry() -> None:
    store = _store()
    session = store.create(locale="en-GB")
    session.last_seen_at = session.created_at + 550

    store.prune(now=session.created_at + 601)

    assert store.get(session.session_id) is None


def test_deleted_session_is_unusable() -> None:
    store = _store()
    session = store.create(locale="en-GB")

    store.delete(session.session_id)

    assert store.get(session.session_id) is None


def test_store_evicts_least_recently_used_session_at_capacity() -> None:
    store = AdminSessionStore(idle_seconds=300, absolute_seconds=600, max_sessions=2)
    first = store.create(locale="en-GB")
    second = store.create(locale="en-GB")
    second.last_seen_at += 1

    third = store.create(locale="en-GB")

    assert store.get(first.session_id) is None
    assert store.get(second.session_id) is second
    assert store.get(third.session_id) is third
