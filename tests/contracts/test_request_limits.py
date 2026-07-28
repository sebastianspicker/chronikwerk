"""Verify request body and token-bucket limits on ingestion routes."""

from __future__ import annotations

# pylint: disable=import-outside-toplevel
from fastapi.testclient import TestClient

from chronikwerk.app.server import create_app
from chronikwerk.config.settings import Settings
from tests.support.process_ticket_helpers import (
    TEST_WEBHOOK_SECRET,
    exhaust_signed_rate_limit,
    install_noop_ingest_processing,
)
from tests.support.settings_factory import make_settings


def _settings_body_limit(storage_root: str, max_bytes: int) -> Settings:
    """Build settings that isolate the body limit scenario."""
    return make_settings(
        storage_root,
        overrides={
            "hardening": {
                "rate_limit": {"enabled": False},
                "body_size_limit": {"max_bytes": max_bytes},
            }
        },
    )


def _settings_rate_limit(storage_root: str) -> Settings:
    """Build settings that isolate the rate limit scenario."""
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


def test_request_limits_body_over_limit_returns_413(tmp_path) -> None:
    """NFR2: Request body over max_bytes must be rejected with 413."""
    app = create_app(_settings_body_limit(str(tmp_path), max_bytes=10))
    client = TestClient(app)
    resp = client.post(
        "/ingest",
        content=b'{"ticket":{"id":123}}',
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 413
    assert resp.json() == {"detail": "request_too_large", "code": "request_too_large"}


def test_request_limits_rate_limit_returns_429(tmp_path, monkeypatch) -> None:
    """NFR2: Ingest over rate limit must be rejected with 429."""
    app = create_app(_settings_rate_limit(str(tmp_path)))
    install_noop_ingest_processing(monkeypatch)
    client = TestClient(app)
    payload = {"ticket": {"id": 1}}
    resp = exhaust_signed_rate_limit(client, "/ingest", payload)
    assert resp.status_code == 429
    assert resp.json() == {"detail": "rate_limited", "code": "rate_limited"}
