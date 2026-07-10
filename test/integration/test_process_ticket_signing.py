from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
import respx

from test.support.credentials import fake_credential  # pylint: disable=wrong-import-order
from test.support.process_ticket_helpers import (  # pylint: disable=wrong-import-order
    archive_article_json,
    expected_process_ticket_pdf_path,
    fixed_process_ticket_now,
    process_ticket_request_payload,
    register_process_ticket_article_route,
    register_process_ticket_fetch_routes,
    register_process_ticket_tag_routes,
)
from test.support.signing_test_helpers import write_test_pfx  # pylint: disable=wrong-import-order
from zammad_pdf_archiver._version import VERSION
from zammad_pdf_archiver.app.jobs import process_ticket as process_ticket_module
from zammad_pdf_archiver.app.jobs.process_ticket import process_ticket
from zammad_pdf_archiver.config.settings import Settings

pytest.importorskip("pyhanko", reason="Signing integration requires pyHanko")


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


def _called_items(route: respx.Route) -> set[str]:
    return {json.loads(call.request.content.decode("utf-8"))["item"] for call in route.calls}


def test_process_ticket_signing_writes_signed_pdf_and_audit_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pfx_path = tmp_path / "test.pfx"
    expected_fingerprint = write_test_pfx(pfx_path, password=fake_credential("secret"))
    settings = _test_settings(str(tmp_path), pfx_path=pfx_path, password=fake_credential("secret"))
    fixed_now = fixed_process_ticket_now(monkeypatch, process_ticket_module)
    payload = process_ticket_request_payload("req-sign-1")

    with respx.mock:
        register_process_ticket_fetch_routes(articles=[archive_article_json()])
        register_process_ticket_tag_routes()
        register_process_ticket_article_route(
            {
                "id": 999,
                "internal": True,
                "subject": "ok",
                "body": "<p>ok</p>",
            }
        )

        asyncio.run(process_ticket("delivery-sign-1", payload, settings))

        expected_pdf_path = expected_process_ticket_pdf_path(tmp_path, settings, fixed_now)
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
    return register_process_ticket_article_route(
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
    write_test_pfx(pfx_path, password=fake_credential("secret"))
    tsa_url = "https://tsa.test/rfc3161"
    settings = _test_settings_with_unreachable_tsa(
        str(tmp_path),
        pfx_path=pfx_path,
        password=fake_credential("secret"),
        tsa_url=tsa_url,
    )
    fixed_process_ticket_now(monkeypatch, process_ticket_module)
    payload = process_ticket_request_payload("req-sign-tsa-err-1")

    with respx.mock:
        respx.post(tsa_url).mock(side_effect=httpx.ConnectError("boom"))
        register_process_ticket_fetch_routes(articles=[archive_article_json()])
        remove_tag_route, add_tag_route = register_process_ticket_tag_routes()
        article_route = _register_ok_article_route()

        asyncio.run(process_ticket("delivery-sign-tsa-err-1", payload, settings))
        _assert_transient_signing_error(remove_tag_route, add_tag_route, article_route)


def test_process_ticket_signing_with_invalid_pfx_password_is_permanent_and_drops_trigger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pfx_path = tmp_path / "test.pfx"
    write_test_pfx(pfx_path, password=fake_credential("secret"))
    settings = _test_settings(
        str(tmp_path), pfx_path=pfx_path, password=fake_credential("wrong-password")
    )
    fixed_process_ticket_now(monkeypatch, process_ticket_module)
    payload = process_ticket_request_payload("req-sign-bad-pass-1")

    with respx.mock:
        register_process_ticket_fetch_routes(articles=[])
        remove_tag_route, add_tag_route = register_process_ticket_tag_routes()
        article_route = register_process_ticket_article_route()

        asyncio.run(process_ticket("delivery-sign-bad-pass-1", payload, settings))

        assert not list(tmp_path.rglob("*.pdf"))
        assert not list(tmp_path.rglob("*.pdf.json"))

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
    write_test_pfx(pfx_path, password=fake_credential("secret"))
    tsa_url = "https://tsa.test/rfc3161"
    settings = _test_settings_with_unreachable_tsa(
        str(tmp_path),
        pfx_path=pfx_path,
        password=fake_credential("secret"),
        tsa_url=tsa_url,
    )
    fixed_process_ticket_now(monkeypatch, process_ticket_module)
    payload = process_ticket_request_payload("req-sign-tsa-503-1")

    with respx.mock:
        respx.post(tsa_url).mock(return_value=httpx.Response(503))
        register_process_ticket_fetch_routes(articles=[])
        remove_tag_route, add_tag_route = register_process_ticket_tag_routes()
        article_route = register_process_ticket_article_route()

        asyncio.run(process_ticket("delivery-sign-tsa-503-1", payload, settings))
        _assert_transient_signing_error(
            remove_tag_route,
            add_tag_route,
            article_route,
            body_contains="HTTP 503",
        )
