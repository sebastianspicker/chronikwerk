from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx

from zammad_pdf_archiver._version import VERSION
from zammad_pdf_archiver.adapters.storage.layout import build_filename_from_pattern
from zammad_pdf_archiver.app.jobs import process_ticket as process_ticket_module
from zammad_pdf_archiver.app.jobs.process_ticket import process_ticket
from zammad_pdf_archiver.config.settings import Settings

pytest.importorskip("pyhanko", reason="Signing integration requires pyHanko")


def _write_test_pfx(path: Path, password: str) -> str:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Integration Test Signer")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(private_key=key, algorithm=hashes.SHA256())
    )

    pfx = pkcs12.serialize_key_and_certificates(
        name=b"test-signer",
        key=key,
        cert=cert,
        cas=None,
        encryption_algorithm=serialization.BestAvailableEncryption(password.encode("utf-8")),
    )
    path.write_bytes(pfx)
    return cert.fingerprint(hashes.SHA256()).hex()


def _test_settings(storage_root: str, *, pfx_path: Path, password: str) -> Settings:
    return Settings.from_mapping(
        {
            "zammad": {"base_url": "https://zammad.example.local", "api_token": "test-token"},
            "storage": {"root": storage_root},
            "hardening": {"transport": {"allow_private_networks": True}},
            "signing": {
                "enabled": True,
                "pfx_path": str(pfx_path),
                "pfx_password": password,
            },
        }
    )


def _test_settings_with_unreachable_tsa(
    storage_root: str, *, pfx_path: Path, password: str, tsa_url: str
) -> Settings:
    return Settings.from_mapping(
        {
            "zammad": {"base_url": "https://zammad.example.local", "api_token": "test-token"},
            "storage": {"root": storage_root},
            "hardening": {"transport": {"allow_private_networks": True}},
            "signing": {
                "enabled": True,
                "pfx_path": str(pfx_path),
                "pfx_password": password,
                "timestamp": {
                    "enabled": True,
                    "rfc3161": {"tsa_url": tsa_url, "timeout_seconds": 0.1},
                },
            },
        }
    )


def _fixed_now(monkeypatch: pytest.MonkeyPatch) -> datetime:
    fixed = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(process_ticket_module, "now_utc", lambda: fixed)
    return fixed


def _payload(request_id: str) -> dict[str, object]:
    return {
        "ticket": {"id": 123},
        "_request_id": request_id,
        "user": {"login": "agent-from-webhook"},
    }


def _ticket_json() -> dict[str, object]:
    return {
        "id": 123,
        "number": "20240123",
        "title": "Example Ticket",
        "owner": {"login": "agent"},
        "updated_by": {"login": "fallback-agent"},
        "preferences": {
            "custom_fields": {
                "archive_user_mode": "owner",
                "archive_path": "A > B > C",
            }
        },
    }


def _article_json() -> dict[str, object]:
    return {
        "id": 1,
        "created_at": "2026-02-07T11:59:00Z",
        "internal": False,
        "subject": "Hello",
        "body": "<p>Hello World</p>",
        "content_type": "text/html",
        "from": "customer@example.invalid",
        "attachments": [],
    }


def _register_fetch_routes(*, articles: list[dict[str, object]]) -> None:
    respx.get("https://zammad.example.local/api/v1/tickets/123").mock(
        return_value=httpx.Response(200, json=_ticket_json())
    )
    respx.get(
        "https://zammad.example.local/api/v1/tags",
        params={"object": "Ticket", "o_id": "123"},
    ).mock(return_value=httpx.Response(200, json=["pdf:sign"]))
    respx.get("https://zammad.example.local/api/v1/ticket_articles/by_ticket/123").mock(
        return_value=httpx.Response(200, json=articles)
    )


def _register_tag_routes() -> tuple[respx.Route, respx.Route]:
    remove_tag_route = respx.post("https://zammad.example.local/api/v1/tags/remove").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    add_tag_route = respx.post("https://zammad.example.local/api/v1/tags/add").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    return remove_tag_route, add_tag_route


def _register_article_route(json_body: dict[str, object] | None = None) -> respx.Route:
    return respx.post("https://zammad.example.local/api/v1/ticket_articles").mock(
        return_value=httpx.Response(200, json=json_body or {"id": 999})
    )


def _called_items(route: respx.Route) -> set[str]:
    return {
        json.loads(call.request.content.decode("utf-8"))["item"]
        for call in route.calls
    }


def _expected_pdf_path(tmp_path: Path, settings: Settings, fixed_now: datetime) -> Path:
    expected_filename = build_filename_from_pattern(
        settings.storage.filename_pattern,
        ticket_number="20240123",
        timestamp_utc=fixed_now.date().isoformat(),
    )
    return tmp_path / "agent" / "A" / "B" / "C" / expected_filename


def test_process_ticket_signing_writes_signed_pdf_and_audit_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pfx_path = tmp_path / "test.pfx"
    expected_fingerprint = _write_test_pfx(pfx_path, password="secret")
    settings = _test_settings(str(tmp_path), pfx_path=pfx_path, password="secret")
    fixed_now = _fixed_now(monkeypatch)
    payload = _payload("req-sign-1")

    with respx.mock:
        _register_fetch_routes(articles=[_article_json()])
        _register_tag_routes()
        _register_article_route(
            {
                "id": 999,
                "internal": True,
                "subject": "ok",
                "body": "<p>ok</p>",
            }
        )

        asyncio.run(process_ticket("delivery-sign-1", payload, settings))

        expected_pdf_path = _expected_pdf_path(tmp_path, settings, fixed_now)
        expected_sidecar_path = expected_pdf_path.parent / (expected_pdf_path.name + ".json")

        assert expected_pdf_path.exists()
        assert expected_sidecar_path.exists()

        pdf_bytes = expected_pdf_path.read_bytes()
        assert pdf_bytes.startswith(b"%PDF")
        assert b"/ByteRange" in pdf_bytes

        audit = json.loads(expected_sidecar_path.read_text("utf-8"))
        assert audit["signing"]["enabled"] is True
        assert audit["signing"]["tsa_used"] is False
        assert audit["signing"]["cert_fingerprint"] == expected_fingerprint


def _register_ok_article_route() -> respx.Route:
    return _register_article_route(
        {
            "id": 999,
            "internal": True,
            "subject": "ok",
            "body": "<p>ok</p>",
        }
    )


def _assert_error_note(
    article_route: respx.Route,
    *,
    permanence: str,
    body_contains: str = "",
) -> None:
    assert article_route.called
    req = json.loads(article_route.calls[0].request.content.decode("utf-8"))
    assert f"PDF archiver error ({VERSION})" in req["subject"]
    assert permanence in req["body"]
    if body_contains:
        assert body_contains in req["body"]


def _assert_transient_signing_error(
    remove_tag_route: respx.Route,
    add_tag_route: respx.Route,
    article_route: respx.Route,
    *,
    body_contains: str = "",
) -> None:
    removed = _called_items(remove_tag_route)
    added = _called_items(add_tag_route)

    assert "pdf:processing" in removed
    assert "pdf:sign" in added
    assert "pdf:error" in added
    _assert_error_note(article_route, permanence="Transient", body_contains=body_contains)


def test_process_ticket_signing_with_unreachable_tsa_is_transient_and_keeps_trigger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pfx_path = tmp_path / "test.pfx"
    _write_test_pfx(pfx_path, password="secret")
    tsa_url = "https://tsa.test/rfc3161"
    settings = _test_settings_with_unreachable_tsa(
        str(tmp_path),
        pfx_path=pfx_path,
        password="secret",
        tsa_url=tsa_url,
    )
    _fixed_now(monkeypatch)
    payload = _payload("req-sign-tsa-err-1")

    with respx.mock:
        respx.post(tsa_url).mock(side_effect=httpx.ConnectError("boom"))
        _register_fetch_routes(articles=[_article_json()])
        remove_tag_route, add_tag_route = _register_tag_routes()
        article_route = _register_ok_article_route()

        asyncio.run(process_ticket("delivery-sign-tsa-err-1", payload, settings))
        _assert_transient_signing_error(remove_tag_route, add_tag_route, article_route)


def test_process_ticket_signing_with_invalid_pfx_password_is_permanent_and_drops_trigger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pfx_path = tmp_path / "test.pfx"
    _write_test_pfx(pfx_path, password="secret")
    settings = _test_settings(str(tmp_path), pfx_path=pfx_path, password="wrong-password")
    _fixed_now(monkeypatch)
    payload = _payload("req-sign-bad-pass-1")

    with respx.mock:
        _register_fetch_routes(articles=[])
        remove_tag_route, add_tag_route = _register_tag_routes()
        article_route = _register_article_route()

        asyncio.run(process_ticket("delivery-sign-bad-pass-1", payload, settings))

        assert list(tmp_path.rglob("*.pdf")) == []
        assert list(tmp_path.rglob("*.pdf.json")) == []

        removed = _called_items(remove_tag_route)
        added = _called_items(add_tag_route)

        assert "pdf:processing" in added
        assert "pdf:done" not in added
        assert "pdf:error" in added
        assert "pdf:sign" not in added

        assert "pdf:processing" in removed
        assert "pdf:sign" in removed
        _assert_error_note(article_route, permanence="Permanent", body_contains="PKCS#12")


def test_process_ticket_signing_with_tsa_http_503_is_transient_and_keeps_trigger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pfx_path = tmp_path / "test.pfx"
    _write_test_pfx(pfx_path, password="secret")
    tsa_url = "https://tsa.test/rfc3161"
    settings = _test_settings_with_unreachable_tsa(
        str(tmp_path),
        pfx_path=pfx_path,
        password="secret",
        tsa_url=tsa_url,
    )
    _fixed_now(monkeypatch)
    payload = _payload("req-sign-tsa-503-1")

    with respx.mock:
        respx.post(tsa_url).mock(return_value=httpx.Response(503))
        _register_fetch_routes(articles=[])
        remove_tag_route, add_tag_route = _register_tag_routes()
        article_route = _register_article_route()

        asyncio.run(process_ticket("delivery-sign-tsa-503-1", payload, settings))
        _assert_transient_signing_error(
            remove_tag_route,
            add_tag_route,
            article_route,
            body_contains="HTTP 503",
        )
