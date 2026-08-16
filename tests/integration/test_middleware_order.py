"""Verifies request-size rejection runs before HMAC verification."""

from __future__ import annotations

from fastapi.testclient import TestClient

from chronikwerk.app.server import create_app
from chronikwerk.config.settings import Settings
from tests.support.hmac_test_helpers import sign_body
from tests.support.http_security_test_helpers import assert_json_error, make_body_limit_settings


def _test_settings(storage_root: str) -> Settings:
    """Build settings isolated to this test scenario."""
    return make_body_limit_settings(
        storage_root,
        10,
        secret="test-secret",
    )


def test_body_size_limit_triggers_before_hmac_verification(tmp_path) -> None:
    app = create_app(_test_settings(str(tmp_path)))
    client = TestClient(app)

    body = b"x" * 100

    # Signature is well-formed but wrong for the actual body.
    signature = sign_body(b"wrong-body", "test-secret")
    response = client.post(
        "/ingest",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature": signature,
        },
    )

    assert_json_error(response, status_code=413, code="request_too_large")
