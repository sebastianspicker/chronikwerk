"""Verifies HMAC validation, delivery binding, and replay resistance at the HTTP boundary."""

from __future__ import annotations

import hashlib
import hmac

from fastapi.testclient import TestClient

from tests.support.hmac_test_helpers import sign_body, sign_strict
from tests.support.http_security_test_helpers import create_ingest_app, post_ingest
from tests.support.process_ticket_helpers import noop_process_ticket


def test_valid_sha256_signature_passes(tmp_path, monkeypatch) -> None:
    secret = "test-secret"
    app = create_ingest_app(str(tmp_path), secret=secret)

    import chronikwerk.app.routes.ingest as ingest_route

    monkeypatch.setattr(ingest_route, "process_ticket", noop_process_ticket)
    client = TestClient(app)
    body = b'{"ticket_id":123}'

    response = post_ingest(client, body, sign_body(body, secret))

    assert response.status_code == 202


def test_strict_delivery_id_rejects_legacy_body_only_signature(tmp_path, monkeypatch) -> None:
    secret = "test-secret"
    app = create_ingest_app(str(tmp_path), secret=secret, require_delivery_id=True)

    import chronikwerk.app.routes.ingest as ingest_route

    monkeypatch.setattr(ingest_route, "process_ticket", noop_process_ticket)
    body = b'{"ticket_id":123}'

    response = post_ingest(
        TestClient(app),
        body,
        sign_body(body, secret),
        delivery_id="fresh-delivery-id",
    )

    assert response.status_code == 403


def test_strict_delivery_id_accepts_bound_signature(tmp_path, monkeypatch) -> None:
    secret = "test-secret"
    app = create_ingest_app(str(tmp_path), secret=secret, require_delivery_id=True)

    import chronikwerk.app.routes.ingest as ingest_route

    monkeypatch.setattr(ingest_route, "process_ticket", noop_process_ticket)
    body = b'{"ticket_id":123}'
    delivery_id = " delivery-123 "

    response = post_ingest(
        TestClient(app),
        body,
        sign_strict(delivery_id, body, secret),
        delivery_id=delivery_id,
    )

    assert response.status_code == 202


def test_strict_signature_cannot_be_replayed_with_a_different_delivery_id(tmp_path) -> None:
    secret = "test-secret"
    app = create_ingest_app(str(tmp_path), secret=secret, require_delivery_id=True)
    body = b'{"ticket_id":123}'

    response = post_ingest(
        TestClient(app),
        body,
        sign_strict("original-delivery", body, secret),
        delivery_id="replayed-delivery",
    )

    assert response.status_code == 403


def test_non_strict_delivery_id_accepts_legacy_body_only_signature(tmp_path, monkeypatch) -> None:
    secret = "test-secret"
    app = create_ingest_app(str(tmp_path), secret=secret)

    import chronikwerk.app.routes.ingest as ingest_route

    monkeypatch.setattr(ingest_route, "process_ticket", noop_process_ticket)
    body = b'{"ticket_id":123}'

    response = post_ingest(
        TestClient(app),
        body,
        sign_body(body, secret),
        delivery_id="legacy-delivery-id",
    )

    assert response.status_code == 202


def test_missing_signature_fails(tmp_path) -> None:
    app = create_ingest_app(str(tmp_path), secret="test-secret")
    response = TestClient(app).post("/ingest", json={"ticket_id": 123})
    assert response.status_code == 403


def test_no_secret_fails_closed(tmp_path) -> None:
    app = create_ingest_app(str(tmp_path), secret=None)
    response = TestClient(app).post("/ingest", json={"ticket_id": 123})
    assert response.status_code == 503


def test_sha1_signature_is_rejected(tmp_path) -> None:
    secret = "test-secret"
    app = create_ingest_app(str(tmp_path), secret=secret)
    body = b'{"ticket_id":123}'
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha1).hexdigest()

    response = post_ingest(TestClient(app), body, f"sha1={digest}")

    assert response.status_code == 403
