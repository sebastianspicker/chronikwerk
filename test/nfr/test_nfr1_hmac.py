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


def test_nfr1_invalid_signature_returns_403(tmp_path) -> None:
    app = create_app(make_settings(str(tmp_path), secret="test-secret"))
    body = b'{"ticket_id":123}'
    response = TestClient(app).post(
        "/ingest",
        content=body,
        headers={"Content-Type": "application/json", "X-Hub-Signature": _sign(body, "wrong")},
    )
    assert response.status_code == 403


def test_nfr1_no_secret_returns_503(tmp_path) -> None:
    app = create_app(make_settings(str(tmp_path), secret=None))
    response = TestClient(app).post("/ingest", json={"ticket_id": 123})
    assert response.status_code == 503


def test_nfr1_valid_signature_returns_202(tmp_path, monkeypatch) -> None:
    app = create_app(make_settings(str(tmp_path), secret="test-secret"))

    import zammad_pdf_archiver.app.routes.ingest as ingest_route

    monkeypatch.setattr(ingest_route, "process_ticket", noop_process_ticket)
    body = b'{"ticket_id":123}'
    response = TestClient(app).post(
        "/ingest",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature": _sign(body, "test-secret"),
        },
    )
    assert response.status_code == 202
