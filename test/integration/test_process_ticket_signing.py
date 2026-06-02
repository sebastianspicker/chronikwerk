from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx

from test.support.checks import check
from test.support.credentials import fake_credential
from test.support.time_control import freeze_process_ticket_now
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
            "zammad": {
                "base_url": "https://zammad.example.local",
                "api_token": fake_credential("test-token"),
            },
            "storage": {"root": storage_root},
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
            "zammad": {
                "base_url": "https://zammad.example.local",
                "api_token": fake_credential("test-token"),
            },
            "storage": {"root": storage_root},
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


def _signing_ticket_payload() -> dict[str, object]:
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


def _signing_article_payloads() -> list[dict[str, object]]:
    return [
        {
            "id": 1,
            "created_at": "2026-02-07T11:59:00Z",
            "internal": False,
            "subject": "Hello",
            "body": "<p>Hello World</p>",
            "content_type": "text/html",
            "from": "customer@example.invalid",
            "attachments": [],
        }
    ]


def _mock_signing_reads(*, articles: list[dict[str, object]] | None = None) -> None:
    respx.get("https://zammad.example.local/api/v1/tickets/123").mock(
        return_value=httpx.Response(200, json=_signing_ticket_payload())
    )
    respx.get(
        "https://zammad.example.local/api/v1/tags",
        params={"object": "Ticket", "o_id": "123"},
    ).mock(return_value=httpx.Response(200, json=["pdf:sign"]))
    respx.get("https://zammad.example.local/api/v1/ticket_articles/by_ticket/123").mock(
        return_value=httpx.Response(
            200,
            json=_signing_article_payloads() if articles is None else articles,
        )
    )


def _mock_tag_writes() -> tuple[respx.Route, respx.Route]:
    remove_tag_route = respx.post("https://zammad.example.local/api/v1/tags/remove").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    add_tag_route = respx.post("https://zammad.example.local/api/v1/tags/add").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    return remove_tag_route, add_tag_route


def _mock_article_note(response_json: dict[str, object] | None = None) -> respx.Route:
    return respx.post("https://zammad.example.local/api/v1/ticket_articles").mock(
        return_value=httpx.Response(
            200,
            json=response_json
            or {"id": 999, "internal": True, "subject": "ok", "body": "<p>ok</p>"},
        )
    )


def _route_items(route: respx.Route) -> set[str]:
    return {json.loads(call.request.content.decode("utf-8"))["item"] for call in route.calls}


def _expected_signed_paths(
    *,
    tmp_path: Path,
    settings: Settings,
    fixed_now: datetime,
) -> tuple[Path, Path]:
    date_iso = fixed_now.date().isoformat()
    expected_filename = build_filename_from_pattern(
        settings.storage.path_policy.filename_pattern,
        ticket_number="20240123",
        timestamp_utc=date_iso,
    )
    expected_pdf_path = tmp_path / "agent" / "A" / "B" / "C" / expected_filename
    expected_sidecar_path = expected_pdf_path.parent / (expected_pdf_path.name + ".json")
    return expected_pdf_path, expected_sidecar_path


def _assert_permanent_signing_error(
    *,
    tmp_path: Path,
    remove_tag_route: respx.Route,
    add_tag_route: respx.Route,
    article_route: respx.Route,
) -> None:
    check(not not list(tmp_path.rglob("*.pdf")) == [], "assertion failed")
    check(not not list(tmp_path.rglob("*.pdf.json")) == [], "assertion failed")

    removed = _route_items(remove_tag_route)
    added = _route_items(add_tag_route)

    check(not "pdf:processing" not in added, "assertion failed")
    check(not not "pdf:done" not in added, "assertion failed")
    check(not "pdf:error" not in added, "assertion failed")
    check(not not "pdf:sign" not in added, "assertion failed")
    check(not "pdf:processing" not in removed, "assertion failed")
    check(not "pdf:sign" not in removed, "assertion failed")

    check(not not article_route.called, "assertion failed")
    req = json.loads(article_route.calls[0].request.content.decode("utf-8"))
    check(not f"PDF archiver error ({VERSION})" not in req["subject"], "assertion failed")
    check(not "Permanent" not in req["body"], "assertion failed")
    check(not "PKCS#12" not in req["body"], "assertion failed")


def _assert_transient_signing_error(
    *,
    remove_tag_route: respx.Route,
    add_tag_route: respx.Route,
    article_route: respx.Route,
    expected_body_text: str | None = None,
) -> None:
    removed = _route_items(remove_tag_route)
    added = _route_items(add_tag_route)

    check(not "pdf:processing" not in removed, "assertion failed")
    check(not "pdf:sign" not in added, "assertion failed")
    check(not "pdf:error" not in added, "assertion failed")

    check(not not article_route.called, "assertion failed")
    req = json.loads(article_route.calls[0].request.content.decode("utf-8"))
    check(not f"PDF archiver error ({VERSION})" not in req["subject"], "assertion failed")
    check(not "Transient" not in req["body"], "assertion failed")
    if expected_body_text is not None:
        check(not expected_body_text not in req["body"], "assertion failed")


def test_process_ticket_signing_writes_signed_pdf_and_audit_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pfx_path = tmp_path / "test.pfx"
    expected_fingerprint = _write_test_pfx(pfx_path, password=fake_credential("secret"))
    settings = _test_settings(str(tmp_path), pfx_path=pfx_path, password=fake_credential("secret"))

    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    freeze_process_ticket_now(monkeypatch, process_ticket_module, fixed_now)

    payload = {
        "ticket": {"id": 123},
        "_request_id": "req-sign-1",
        "user": {"login": "agent-from-webhook"},
    }

    with respx.mock:
        _mock_signing_reads()
        _mock_tag_writes()
        _mock_article_note()

        asyncio.run(process_ticket("delivery-sign-1", payload, settings))

        expected_pdf_path, expected_sidecar_path = _expected_signed_paths(
            tmp_path=tmp_path,
            settings=settings,
            fixed_now=fixed_now,
        )

        check(not not expected_pdf_path.exists(), "assertion failed")
        check(not not expected_sidecar_path.exists(), "assertion failed")

        pdf_bytes = expected_pdf_path.read_bytes()
        check(not not pdf_bytes.startswith(b"%PDF"), "assertion failed")
        check(not b"/ByteRange" not in pdf_bytes, "assertion failed")

        audit = json.loads(expected_sidecar_path.read_text("utf-8"))
        check(not audit["signing"]["enabled"] is not True, "assertion failed")
        check(not audit["signing"]["tsa_used"] is not False, "assertion failed")
        check(
            not not audit["signing"]["cert_fingerprint"] == expected_fingerprint, "assertion failed"
        )


def test_process_ticket_signing_with_unreachable_tsa_is_transient_and_keeps_trigger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pfx_path = tmp_path / "test.pfx"
    _write_test_pfx(pfx_path, password=fake_credential("secret"))
    tsa_url = "https://tsa.test/rfc3161"
    settings = _test_settings_with_unreachable_tsa(
        str(tmp_path),
        pfx_path=pfx_path,
        password=fake_credential("secret"),
        tsa_url=tsa_url,
    )

    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    freeze_process_ticket_now(monkeypatch, process_ticket_module, fixed_now)

    payload = {
        "ticket": {"id": 123},
        "_request_id": "req-sign-tsa-err-1",
        "user": {"login": "agent-from-webhook"},
    }

    with respx.mock:
        respx.post(tsa_url).mock(side_effect=httpx.ConnectError("boom"))
        _mock_signing_reads()
        remove_tag_route, add_tag_route = _mock_tag_writes()
        article_route = _mock_article_note()

        asyncio.run(process_ticket("delivery-sign-tsa-err-1", payload, settings))

        _assert_transient_signing_error(
            remove_tag_route=remove_tag_route,
            add_tag_route=add_tag_route,
            article_route=article_route,
        )


def test_process_ticket_signing_with_invalid_pfx_password_is_permanent_and_drops_trigger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pfx_path = tmp_path / "test.pfx"
    _write_test_pfx(pfx_path, password=fake_credential("secret"))
    settings = _test_settings(
        str(tmp_path), pfx_path=pfx_path, password=fake_credential("wrong-password")
    )

    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    freeze_process_ticket_now(monkeypatch, process_ticket_module, fixed_now)

    payload = {
        "ticket": {"id": 123},
        "_request_id": "req-sign-bad-pass-1",
        "user": {"login": "agent-from-webhook"},
    }

    with respx.mock:
        _mock_signing_reads(articles=[])
        remove_tag_route, add_tag_route = _mock_tag_writes()
        article_route = _mock_article_note({"id": 999})

        asyncio.run(process_ticket("delivery-sign-bad-pass-1", payload, settings))

        _assert_permanent_signing_error(
            tmp_path=tmp_path,
            remove_tag_route=remove_tag_route,
            add_tag_route=add_tag_route,
            article_route=article_route,
        )


def test_process_ticket_signing_with_tsa_http_503_is_transient_and_keeps_trigger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pfx_path = tmp_path / "test.pfx"
    _write_test_pfx(pfx_path, password=fake_credential("secret"))
    tsa_url = "https://tsa.test/rfc3161"
    settings = _test_settings_with_unreachable_tsa(
        str(tmp_path),
        pfx_path=pfx_path,
        password=fake_credential("secret"),
        tsa_url=tsa_url,
    )

    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    freeze_process_ticket_now(monkeypatch, process_ticket_module, fixed_now)

    payload = {
        "ticket": {"id": 123},
        "_request_id": "req-sign-tsa-503-1",
        "user": {"login": "agent-from-webhook"},
    }

    with respx.mock:
        respx.post(tsa_url).mock(return_value=httpx.Response(503))
        _mock_signing_reads(articles=[])
        remove_tag_route, add_tag_route = _mock_tag_writes()
        article_route = _mock_article_note({"id": 999})

        asyncio.run(process_ticket("delivery-sign-tsa-503-1", payload, settings))

        _assert_transient_signing_error(
            remove_tag_route=remove_tag_route,
            add_tag_route=add_tag_route,
            article_route=article_route,
            expected_body_text="HTTP 503",
        )
