"""Verifies request-size rejection runs before HMAC verification."""

from __future__ import annotations

import hashlib
import hmac

from fastapi.testclient import TestClient

from chronikwerk.app.server import create_app
from chronikwerk.config.settings import Settings
from tests.support.settings_factory import make_settings


def _test_settings(storage_root: str) -> Settings:
    """Build settings isolated to this test scenario."""
    return make_settings(
        storage_root,
        secret="test-secret",
        overrides={"hardening": {"body_size_limit": {"max_bytes": 10}}},
    )


def _signature(body: bytes, secret: str) -> str:
    """Sign a request body so middleware ordering reaches verification logic."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_body_size_limit_triggers_before_hmac_verification(tmp_path) -> None:
    app = create_app(_test_settings(str(tmp_path)))
    client = TestClient(app)

    body = b"x" * 100

    # Signature is well-formed but wrong for the actual body.
    signature = _signature(b"wrong-body", "test-secret")
    response = client.post(
        "/ingest",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature": signature,
        },
    )

    assert response.status_code == 413
    assert response.json() == {"detail": "request_too_large", "code": "request_too_large"}
