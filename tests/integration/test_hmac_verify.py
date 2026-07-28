"""Verifies HMAC validation, delivery binding, and replay resistance at the HTTP boundary."""

from __future__ import annotations

import hashlib
import hmac

from fastapi.testclient import TestClient

from chronikwerk.app.server import create_app
from tests.support.process_ticket_helpers import noop_process_ticket
from tests.support.settings_factory import make_settings


def _sign(body: bytes, secret: str) -> str:
    """Create the HMAC signature expected by the test request."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _sign_strict(delivery_id: str, body: bytes, secret: str) -> str:
    """Sign domain || uint64_be(len(normalized_id_utf8)) || normalized_id_utf8 || body."""
    normalized_id = delivery_id.strip()
    delivery_id_bytes = normalized_id.encode("utf-8")
    canonical = (
        b"zammad-webhook-v1\0"
        + len(delivery_id_bytes).to_bytes(8, byteorder="big")
        + delivery_id_bytes
        + body
    )
    digest = hmac.new(secret.encode("utf-8"), canonical, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_valid_sha256_signature_passes(tmp_path, monkeypatch) -> None:
    secret = "test-secret"
    app = create_app(make_settings(str(tmp_path), secret=secret))

    import chronikwerk.app.routes.ingest as ingest_route

    monkeypatch.setattr(ingest_route, "process_ticket", noop_process_ticket)
    client = TestClient(app)
    body = b'{"ticket_id":123}'

    response = client.post(
        "/ingest",
        content=body,
        headers={"Content-Type": "application/json", "X-Hub-Signature": _sign(body, secret)},
    )

    assert response.status_code == 202


def test_strict_delivery_id_rejects_legacy_body_only_signature(tmp_path, monkeypatch) -> None:
    secret = "test-secret"
    app = create_app(make_settings(str(tmp_path), secret=secret, require_delivery_id=True))

    import chronikwerk.app.routes.ingest as ingest_route

    monkeypatch.setattr(ingest_route, "process_ticket", noop_process_ticket)
    body = b'{"ticket_id":123}'

    response = TestClient(app).post(
        "/ingest",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature": _sign(body, secret),
            "X-Zammad-Delivery": "fresh-delivery-id",
        },
    )

    assert response.status_code == 403


def test_strict_delivery_id_accepts_bound_signature(tmp_path, monkeypatch) -> None:
    secret = "test-secret"
    app = create_app(make_settings(str(tmp_path), secret=secret, require_delivery_id=True))

    import chronikwerk.app.routes.ingest as ingest_route

    monkeypatch.setattr(ingest_route, "process_ticket", noop_process_ticket)
    body = b'{"ticket_id":123}'
    delivery_id = " delivery-123 "

    response = TestClient(app).post(
        "/ingest",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature": _sign_strict(delivery_id, body, secret),
            "X-Zammad-Delivery": delivery_id,
        },
    )

    assert response.status_code == 202


def test_strict_signature_cannot_be_replayed_with_a_different_delivery_id(tmp_path) -> None:
    secret = "test-secret"
    app = create_app(make_settings(str(tmp_path), secret=secret, require_delivery_id=True))
    body = b'{"ticket_id":123}'

    response = TestClient(app).post(
        "/ingest",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature": _sign_strict("original-delivery", body, secret),
            "X-Zammad-Delivery": "replayed-delivery",
        },
    )

    assert response.status_code == 403


def test_non_strict_delivery_id_accepts_legacy_body_only_signature(tmp_path, monkeypatch) -> None:
    secret = "test-secret"
    app = create_app(make_settings(str(tmp_path), secret=secret))

    import chronikwerk.app.routes.ingest as ingest_route

    monkeypatch.setattr(ingest_route, "process_ticket", noop_process_ticket)
    body = b'{"ticket_id":123}'

    response = TestClient(app).post(
        "/ingest",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature": _sign(body, secret),
            "X-Zammad-Delivery": "legacy-delivery-id",
        },
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
