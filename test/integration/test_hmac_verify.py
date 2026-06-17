from __future__ import annotations

import hashlib
import hmac

from fastapi.testclient import TestClient

from test.support.process_ticket_helpers import noop_process_ticket
from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.server import create_app


def _sign(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_valid_sha256_signature_passes(tmp_path, monkeypatch) -> None:
    secret = "test-secret"
    app = create_app(make_settings(str(tmp_path), secret=secret))

    import zammad_pdf_archiver.app.routes.ingest as ingest_route

    monkeypatch.setattr(ingest_route, "process_ticket", noop_process_ticket)
    client = TestClient(app)
    body = b'{"ticket_id":123}'

    response = client.post(
        "/ingest",
        content=body,
        headers={"Content-Type": "application/json", "X-Hub-Signature": _sign(body, secret)},
    )

    assert response.status_code == 202


def test_missing_signature_fails(tmp_path) -> None:
    app = create_app(make_settings(str(tmp_path), secret="test-secret"))
    response = TestClient(app).post("/ingest", json={"ticket_id": 123})
    assert response.status_code == 403


def test_no_secret_fails_closed(tmp_path) -> None:
    app = create_app(make_settings(str(tmp_path), secret=None))
    response = TestClient(app).post("/ingest", json={"ticket_id": 123})
    assert response.status_code == 503


def test_sha1_signature_is_rejected(tmp_path) -> None:
    secret = "test-secret"
    app = create_app(make_settings(str(tmp_path), secret=secret))
    body = b'{"ticket_id":123}'
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha1).hexdigest()

    response = TestClient(app).post(
        "/ingest",
        content=body,
        headers={"Content-Type": "application/json", "X-Hub-Signature": f"sha1={digest}"},
    )

    assert response.status_code == 403
