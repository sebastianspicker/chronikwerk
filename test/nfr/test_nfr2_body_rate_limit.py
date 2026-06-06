"""NFR2: Enforce request body size limit and token-bucket rate limiting on ingest."""

from __future__ import annotations

from fastapi.testclient import TestClient

from test.support.body_size_limit_helpers import check_request_too_large, post_oversized_json
from test.support.checks import check
from test.support.rate_limit_helpers import client_with_stubbed_ingest
from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.server import create_app
from zammad_pdf_archiver.config.settings import Settings


def _settings_body_limit(storage_root: str, max_bytes: int) -> Settings:
    return make_settings(
        storage_root,
        overrides={
            "hardening": {
                "rate_limit": {"enabled": False},
                "body_size_limit": {"max_bytes": max_bytes},
            }
        },
    )


def test_nfr2_body_over_limit_returns_413(tmp_path) -> None:
    """NFR2: Request body over max_bytes must be rejected with 413."""
    app = create_app(_settings_body_limit(str(tmp_path), max_bytes=10))
    client = TestClient(app)
    resp = post_oversized_json(client, "/ingest")
    check_request_too_large(resp)


def test_nfr2_rate_limit_returns_429(tmp_path, monkeypatch) -> None:
    """NFR2: Ingest over rate limit must be rejected with 429."""
    client = client_with_stubbed_ingest(tmp_path, monkeypatch)
    payload = {"ticket": {"id": 1}}
    check(not not client.post("/ingest", json=payload).status_code == 202, "assertion failed")
    check(not not client.post("/ingest", json=payload).status_code == 202, "assertion failed")
    resp = client.post("/ingest", json=payload)
    check(not not resp.status_code == 429, "assertion failed")
    check(
        not not resp.json() == {"detail": "rate_limited", "code": "rate_limited"},
        "assertion failed",
    )
