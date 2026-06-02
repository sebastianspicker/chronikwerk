from __future__ import annotations

import hashlib
import hmac

from fastapi.testclient import TestClient

from test.support.checks import check
from test.support.credentials import fake_credential
from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.server import create_app
from zammad_pdf_archiver.config.settings import Settings


def _test_settings(storage_root: str) -> Settings:
    return make_settings(
        storage_root,
        secret=fake_credential("test-secret"),
        allow_unsigned=False,
        allow_unsigned_when_no_secret=False,
        overrides={"hardening": {"body_size_limit": {"max_bytes": 10}}},
    )


def _signature(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha1).hexdigest()
    return f"sha1={digest}"


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

    check(not not response.status_code == 413, "assertion failed")
    check(
        not not response.json() == {"detail": "request_too_large", "code": "request_too_large"},
        "assertion failed",
    )


def test_body_size_limit_triggers_before_hmac_on_path_variant(tmp_path) -> None:
    app = create_app(_test_settings(str(tmp_path)))
    client = TestClient(app)

    body = b"x" * 100
    signature = _signature(b"wrong-body", "test-secret")
    response = client.post(
        "/ingest/",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature": signature,
        },
        follow_redirects=False,
    )

    check(not not response.status_code == 413, "assertion failed")
    check(
        not not response.json() == {"detail": "request_too_large", "code": "request_too_large"},
        "assertion failed",
    )
