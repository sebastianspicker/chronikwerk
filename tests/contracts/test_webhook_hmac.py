"""Verify the webhook HMAC contract at authentication boundaries."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.support.hmac_test_helpers import sign_body
from tests.support.http_security_test_helpers import create_ingest_app, post_ingest
from tests.support.process_ticket_helpers import noop_process_ticket


def test_webhook_hmac_invalid_signature_returns_403(tmp_path) -> None:
    app = create_ingest_app(str(tmp_path), secret="test-secret")
    body = b'{"ticket_id":123}'
    response = post_ingest(TestClient(app), body, sign_body(body, "wrong"))
    assert response.status_code == 403


def test_webhook_hmac_no_secret_returns_503(tmp_path) -> None:
    app = create_ingest_app(str(tmp_path), secret=None)
    response = TestClient(app).post("/ingest", json={"ticket_id": 123})
    assert response.status_code == 503


def test_webhook_hmac_valid_signature_returns_202(tmp_path, monkeypatch) -> None:
    app = create_ingest_app(str(tmp_path), secret="test-secret")

    import chronikwerk.app.routes.ingest as ingest_route

    monkeypatch.setattr(ingest_route, "process_ticket", noop_process_ticket)
    body = b'{"ticket_id":123}'
    response = post_ingest(TestClient(app), body, sign_body(body, "test-secret"))
    assert response.status_code == 202
