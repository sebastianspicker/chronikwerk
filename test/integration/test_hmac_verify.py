from __future__ import annotations

import hashlib
import hmac

import pytest
from fastapi.testclient import TestClient

from test.support.checks import check
from test.support.credentials import fake_credential
from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.server import create_app
from zammad_pdf_archiver.config.settings import Settings


def _test_settings(storage_root: str, *, secret: str | None) -> Settings:
    return make_settings(
        storage_root,
        secret=secret,
        allow_unsigned=False,
        allow_unsigned_when_no_secret=False,
    )


def _test_settings_unsigned_ok(storage_root: str) -> Settings:
    return make_settings(
        storage_root,
        secret=None,
        allow_unsigned=True,
        allow_unsigned_when_no_secret=True,
    )


def _test_settings_unsigned_missing_no_secret_opt_in(storage_root: str) -> Settings:
    return make_settings(
        storage_root,
        secret=None,
        allow_unsigned=True,
        allow_unsigned_when_no_secret=False,
    )


def _test_settings_legacy_secret(storage_root: str, *, secret: str) -> Settings:
    return Settings.from_mapping(
        {
            "server": {"webhook_shared_secret": secret},
            "zammad": {
                "base_url": "https://zammad.example.local",
                "api_token": fake_credential("test-token"),
            },
            "storage": {"root": storage_root},
            "hardening": {"webhook": {"require_delivery_id": False}},
        }
    )


def _sign(body: bytes, secret: str, *, algorithm: str = "sha1") -> str:
    if algorithm == "sha256":
        digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        return f"sha256={digest}"
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha1).hexdigest()
    return f"sha1={digest}"


def test_valid_signature_passes(tmp_path, monkeypatch) -> None:
    secret = fake_credential("test-secret")
    app = create_app(_test_settings(str(tmp_path), secret=secret))
    import zammad_pdf_archiver.app.routes.ingest as ingest_route

    async def _stub_process_ticket(delivery_id, payload, settings) -> None:
        return None

    monkeypatch.setattr(ingest_route, "process_ticket", _stub_process_ticket)
    client = TestClient(app)

    body = b'{"ticket":{"id":123}}'
    response = client.post(
        "/ingest",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature": _sign(body, secret),
        },
    )
    check(not not response.status_code == 202, "assertion failed")
    check(not not response.json() == {"status": "accepted", "ticket_id": 123}, "assertion failed")


def test_signed_ingest_requires_delivery_id_by_default(tmp_path, monkeypatch) -> None:
    secret = fake_credential("test-secret")
    settings = Settings.from_mapping(
        {
            "zammad": {
                "base_url": "https://zammad.example.local",
                "api_token": fake_credential("test-token"),
                "webhook_hmac_secret": secret,
            },
            "storage": {"root": str(tmp_path)},
        }
    )
    app = create_app(settings)
    import zammad_pdf_archiver.app.routes.ingest as ingest_route

    async def _stub_process_ticket(delivery_id, payload, settings) -> None:
        raise AssertionError("process_ticket must not run without delivery ID")

    monkeypatch.setattr(ingest_route, "process_ticket", _stub_process_ticket)
    client = TestClient(app)

    body = b'{"ticket":{"id":123}}'
    response = client.post(
        "/ingest",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature": _sign(body, secret),
        },
    )

    check(not not response.status_code == 400, "assertion failed")
    check(
        not not response.json() == {"detail": "missing_delivery_id", "code": "missing_delivery_id"},
        "assertion failed",
    )


def test_valid_sha256_signature_passes(tmp_path, monkeypatch) -> None:
    secret = fake_credential("test-secret")
    app = create_app(_test_settings(str(tmp_path), secret=secret))
    import zammad_pdf_archiver.app.routes.ingest as ingest_route

    async def _stub_process_ticket(delivery_id, payload, settings) -> None:
        return None

    monkeypatch.setattr(ingest_route, "process_ticket", _stub_process_ticket)
    client = TestClient(app)

    body = b'{"ticket":{"id":456}}'
    response = client.post(
        "/ingest",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature": _sign(body, secret, algorithm="sha256"),
        },
    )
    check(not not response.status_code == 202, "assertion failed")
    check(not not response.json() == {"status": "accepted", "ticket_id": 456}, "assertion failed")


def test_invalid_signature_is_rejected(tmp_path) -> None:
    secret = fake_credential("test-secret")
    app = create_app(_test_settings(str(tmp_path), secret=secret))
    client = TestClient(app)

    body = b'{"ticket_id":123}'
    response = client.post(
        "/ingest",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature": _sign(body, "wrong-secret"),
        },
    )
    check(not not response.status_code == 403, "assertion failed")
    check(not not response.headers.get("X-Request-Id"), "assertion failed")


def test_missing_signature_is_rejected_when_secret_configured(tmp_path) -> None:
    secret = fake_credential("test-secret")
    app = create_app(_test_settings(str(tmp_path), secret=secret))
    client = TestClient(app)

    response = client.post("/ingest", json={"ticket": {"id": 123}})
    check(not not response.status_code == 403, "assertion failed")


def test_missing_signature_is_rejected_on_ingest_path_variants(tmp_path) -> None:
    secret = fake_credential("test-secret")
    app = create_app(_test_settings(str(tmp_path), secret=secret))
    client = TestClient(app)

    for path in ("/ingest/", "/ingest%2F", "/ingest/batch/"):
        response = client.post(path, json={"ticket": {"id": 123}}, follow_redirects=False)
        check(not not response.status_code == 403, "assertion failed")
        check(
            not not response.json() == {"detail": "forbidden", "code": "forbidden"},
            "assertion failed",
        )


def test_missing_signature_is_allowed_when_secret_unset(tmp_path) -> None:
    app = create_app(_test_settings(str(tmp_path), secret=None))
    client = TestClient(app)

    response = client.post("/ingest", json={})
    check(not not response.status_code == 503, "assertion failed")


def test_allow_unsigned_without_no_secret_opt_in_still_fails_closed(tmp_path) -> None:
    app = create_app(_test_settings_unsigned_missing_no_secret_opt_in(str(tmp_path)))
    client = TestClient(app)

    response = client.post("/ingest", json={"ticket": {"id": 1}})

    check(not not response.status_code == 503, "assertion failed")
    check(
        not not response.json()
        == {"detail": "webhook_auth_not_configured", "code": "webhook_auth_not_configured"},
        "assertion failed",
    )


def test_missing_signature_is_allowed_only_when_allow_unsigned_enabled(
    tmp_path, monkeypatch
) -> None:
    async def _stub_process_ticket(delivery_id, payload, settings) -> None:  # noqa: ANN001, ARG001
        return None

    app = create_app(_test_settings_unsigned_ok(str(tmp_path)))
    import zammad_pdf_archiver.app.routes.ingest as ingest_route

    monkeypatch.setattr(ingest_route, "process_ticket", _stub_process_ticket)
    client = TestClient(app)

    response = client.post("/ingest", json={"ticket": {"id": 1}})
    check(not not response.status_code == 202, "assertion failed")


@pytest.mark.parametrize(
    "signature",
    [
        "sha1",  # missing "="
        f"sha256={'00' * 20}",  # wrong algorithm
        "sha1=not-hex",
        "sha1=00",  # wrong length
    ],
)
def test_malformed_signature_is_rejected(tmp_path, signature: str) -> None:
    secret = fake_credential("test-secret")
    app = create_app(_test_settings(str(tmp_path), secret=secret))
    client = TestClient(app)

    body = b'{"ticket":{"id":123}}'
    response = client.post(
        "/ingest",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature": signature,
        },
    )
    check(not not response.status_code == 403, "assertion failed")


def test_signature_must_match_request_body_bytes(tmp_path, monkeypatch) -> None:
    secret = fake_credential("test-secret")
    app = create_app(_test_settings(str(tmp_path), secret=secret))

    import zammad_pdf_archiver.app.routes.ingest as ingest_route

    async def _stub_process_ticket(_delivery_id, _payload, _settings) -> None:
        raise AssertionError("process_ticket must not run when signature verification fails")

    monkeypatch.setattr(ingest_route, "process_ticket", _stub_process_ticket)
    client = TestClient(app)

    body = b'{"ticket":{"id":123}}'
    wrong_body = body + b" "
    response = client.post(
        "/ingest",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature": _sign(wrong_body, secret),
        },
    )
    check(not not response.status_code == 403, "assertion failed")


def test_default_app_fails_closed_without_settings() -> None:
    from zammad_pdf_archiver.app.server import app as default_app

    client = TestClient(default_app)
    response = client.post("/ingest", json={"ticket": {"id": 123}})

    check(not not response.status_code == 503, "assertion failed")
    data = response.json()
    check(
        not not data
        == {"detail": "webhook_auth_not_configured", "code": "webhook_auth_not_configured"},
        "assertion failed",
    )
    check(not not response.headers.get("X-Request-Id"), "assertion failed")


def test_valid_signature_passes_with_legacy_shared_secret(tmp_path, monkeypatch) -> None:
    secret = fake_credential("legacy-secret")
    app = create_app(_test_settings_legacy_secret(str(tmp_path), secret=secret))
    import zammad_pdf_archiver.app.routes.ingest as ingest_route

    async def _stub_process_ticket(delivery_id, payload, settings) -> None:
        return None

    monkeypatch.setattr(ingest_route, "process_ticket", _stub_process_ticket)
    client = TestClient(app)

    body = b'{"ticket":{"id":123}}'
    response = client.post(
        "/ingest",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature": _sign(body, secret),
        },
    )
    check(not not response.status_code == 202, "assertion failed")
    check(not not response.json() == {"status": "accepted", "ticket_id": 123}, "assertion failed")


def test_batch_missing_signature_is_rejected_when_secret_configured(tmp_path) -> None:
    secret = fake_credential("test-secret")
    app = create_app(_test_settings(str(tmp_path), secret=secret))
    client = TestClient(app)

    response = client.post("/ingest/batch", json=[{"ticket": {"id": 123}}])
    check(not not response.status_code == 403, "assertion failed")
    check(not not response.headers.get("X-Request-Id"), "assertion failed")


def test_batch_valid_signature_passes(tmp_path, monkeypatch) -> None:
    secret = fake_credential("test-secret")
    app = create_app(_test_settings(str(tmp_path), secret=secret))
    import zammad_pdf_archiver.app.routes.ingest as ingest_route

    async def _stub_process_ticket(delivery_id, payload, settings) -> None:
        return None

    monkeypatch.setattr(ingest_route, "process_ticket", _stub_process_ticket)
    client = TestClient(app)

    body = b'[{"ticket":{"id":123}}]'
    response = client.post(
        "/ingest/batch",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature": _sign(body, secret),
        },
    )
    check(not not response.status_code == 202, "assertion failed")
    check(not not response.json() == {"status": "accepted", "count": 1}, "assertion failed")
