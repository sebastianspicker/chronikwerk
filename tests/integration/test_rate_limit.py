"""Verifies ingest and batch endpoints enforce configured client rate limits."""

from __future__ import annotations

from fastapi.testclient import TestClient

from chronikwerk.app.server import create_app
from chronikwerk.config.settings import Settings
from tests.support.process_ticket_helpers import (
    TEST_WEBHOOK_SECRET,
    exhaust_signed_rate_limit,
    install_noop_ingest_processing,
)
from tests.support.settings_factory import make_settings


def _test_settings(storage_root: str) -> Settings:
    """Build settings isolated to this test scenario."""
    return make_settings(
        storage_root,
        secret=TEST_WEBHOOK_SECRET,
        overrides={
            "hardening": {
                "rate_limit": {"enabled": True, "rps": 0, "burst": 2},
                "body_size_limit": {"max_bytes": 1024 * 1024},
            }
        },
    )


def test_rate_limit_triggers_on_ingest(tmp_path, monkeypatch) -> None:
    app = create_app(_test_settings(str(tmp_path)))
    install_noop_ingest_processing(monkeypatch)
    client = TestClient(app)

    payload = {"ticket": {"id": 1}}
    resp = exhaust_signed_rate_limit(client, "/ingest", payload)
    assert resp.status_code == 429
    assert resp.json() == {"detail": "rate_limited", "code": "rate_limited"}
    assert resp.headers["connection"] == "close"
    assert resp.headers.get("X-Request-Id")


def test_rate_limit_triggers_on_ingest_batch(tmp_path, monkeypatch) -> None:
    app = create_app(_test_settings(str(tmp_path)))
    install_noop_ingest_processing(monkeypatch)
    client = TestClient(app)

    payload = [{"ticket": {"id": 1}}]
    resp = exhaust_signed_rate_limit(client, "/ingest/batch", payload)
    assert resp.status_code == 429
    assert resp.json() == {"detail": "rate_limited", "code": "rate_limited"}
    assert resp.headers.get("X-Request-Id")


def test_rate_limit_key_from_forwarded_header_unit() -> None:
    """Rate limit key can be taken from X-Forwarded-For (unit: _client_key_from_header)."""
    from chronikwerk.app.middleware.rate_limit import _client_key, _client_key_from_header

    scope_with_header: dict = {
        "type": "http",
        "headers": [(b"x-forwarded-for", b" 203.0.113.1 , 70.41.3.1 ")],
        "client": ["192.168.1.1", 12345],
    }
    assert _client_key_from_header(scope_with_header, "X-Forwarded-For") == "203.0.113.1"
    assert _client_key(scope_with_header, "X-Forwarded-For") == "203.0.113.1"
    assert _client_key(scope_with_header, None) == "192.168.1.1"
