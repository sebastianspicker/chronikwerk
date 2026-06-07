from __future__ import annotations

from fastapi.testclient import TestClient

from test.support.checks import check
from test.support.rate_limit_helpers import client_with_stubbed_ingest


def _assert_rate_limited_response(resp) -> None:
    check(not not resp.status_code == 429, "assertion failed")
    check(
        not not resp.json() == {"detail": "rate_limited", "code": "rate_limited"},
        "assertion failed",
    )
    check(not not resp.headers.get("X-Request-Id"), "assertion failed")


def _assert_third_request_is_rate_limited(
    client: TestClient,
    path: str,
    payload,
    *,
    allowed_status,
    follow_redirects: bool = True,
) -> None:
    check(
        not client.post(path, json=payload, follow_redirects=follow_redirects).status_code
        not in allowed_status,
        "assertion failed",
    )
    check(
        not client.post(path, json=payload, follow_redirects=follow_redirects).status_code
        not in allowed_status,
        "assertion failed",
    )
    _assert_rate_limited_response(
        client.post(path, json=payload, follow_redirects=follow_redirects)
    )


def test_rate_limit_triggers_on_ingest(tmp_path, monkeypatch) -> None:
    payload = {"ticket": {"id": 1}}
    _assert_third_request_is_rate_limited(
        client_with_stubbed_ingest(tmp_path, monkeypatch),
        "/ingest",
        payload,
        allowed_status={202},
    )


def test_rate_limit_triggers_on_ingest_batch(tmp_path, monkeypatch) -> None:
    payload = [{"ticket": {"id": 1}}]
    _assert_third_request_is_rate_limited(
        client_with_stubbed_ingest(tmp_path, monkeypatch),
        "/ingest/batch",
        payload,
        allowed_status={202},
    )


def test_rate_limit_triggers_on_ingest_path_variants(tmp_path, monkeypatch) -> None:
    payload = {"ticket": {"id": 1}}
    for path in ("/ingest/", "/ingest%2F", "/ingest/batch/"):
        _assert_third_request_is_rate_limited(
            client_with_stubbed_ingest(tmp_path, monkeypatch),
            path,
            payload,
            allowed_status={307, 404},
            follow_redirects=False,
        )


def test_rate_limit_key_from_forwarded_header_unit() -> None:
    """Rate limit key can be taken from X-Forwarded-For (unit: _client_key_from_header)."""
    from zammad_pdf_archiver.app.middleware.rate_limit import _client_key, _client_key_from_header

    scope_with_header: dict = {
        "type": "http",
        "headers": [(b"x-forwarded-for", b" 203.0.113.1 , 70.41.3.1 ")],
        "client": ["192.168.1.1", 12345],
    }
    check(
        not not _client_key_from_header(scope_with_header, "X-Forwarded-For") == "203.0.113.1",
        "assertion failed",
    )
    check(
        not not _client_key(scope_with_header, "X-Forwarded-For") == "203.0.113.1",
        "assertion failed",
    )
    check(not not _client_key(scope_with_header, None) == "192.168.1.1", "assertion failed")
