from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

import test.support.integration_helpers as integration_helpers
from test.support.checks import check
from test.support.credentials import fake_credential
from test.support.signing_helpers import write_test_pfx
from test.support.time_control import freeze_process_ticket_now
from zammad_pdf_archiver.adapters.storage.layout import build_filename_from_pattern
from zammad_pdf_archiver.app.jobs import process_ticket as process_ticket_module
from zammad_pdf_archiver.app.jobs.process_ticket import process_ticket
from zammad_pdf_archiver.config.settings import Settings

pytest.importorskip("pyhanko", reason="Signing integration requires pyHanko")


def _signing_settings_mapping(
    storage_root: str,
    *,
    pfx_path: Path,
    password: str,
    timestamp: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "zammad": {
            "base_url": "https://zammad.example.local",
            "api_token": fake_credential("test-token"),
        },
        "storage": {"root": storage_root},
        "signing": {
            "enabled": True,
            "pfx_path": str(pfx_path),
            "pfx_password": password,
            **({} if timestamp is None else {"timestamp": timestamp}),
        },
    }


def _test_settings(storage_root: str, *, pfx_path: Path, password: str) -> Settings:
    return Settings.from_mapping(
        _signing_settings_mapping(storage_root, pfx_path=pfx_path, password=password)
    )


def _test_settings_with_unreachable_tsa(
    storage_root: str, *, pfx_path: Path, password: str, tsa_url: str
) -> Settings:
    return Settings.from_mapping(
        _signing_settings_mapping(
            storage_root,
            pfx_path=pfx_path,
            password=password,
            timestamp={
                "enabled": True,
                "rfc3161": {"tsa_url": tsa_url, "timeout_seconds": 0.1},
            },
        )
    )


def _write_signing_pfx(
    tmp_path: Path,
    *,
    password: str = fake_credential("secret"),
    common_name: str | None = None,
) -> tuple[Path, str]:
    pfx_path = tmp_path / "test.pfx"
    fingerprint = write_test_pfx(
        pfx_path,
        password=password,
        common_name="Test Signer" if common_name is None else common_name,
    )
    return pfx_path, fingerprint


def _test_tsa_settings(tmp_path: Path, *, tsa_url: str) -> Settings:
    pfx_path, _ = _write_signing_pfx(tmp_path)
    return _test_settings_with_unreachable_tsa(
        str(tmp_path),
        pfx_path=pfx_path,
        password=fake_credential("secret"),
        tsa_url=tsa_url,
    )


def _signing_ticket_payload() -> dict[str, object]:
    return integration_helpers.zammad_ticket_payload(
        title="Example Ticket",
        archive_path="A > B > C",
    )


def _signing_article_payloads() -> list[dict[str, object]]:
    return [integration_helpers.zammad_article_payload()]


def _mock_signing_reads(*, articles: list[dict[str, object]] | None = None) -> None:
    integration_helpers.mock_standard_zammad_reads(
        ticket_payload=_signing_ticket_payload(),
        tags=["pdf:sign"],
        articles=_signing_article_payloads() if articles is None else articles,
    )


def _mock_tag_writes() -> tuple[respx.Route, respx.Route]:
    return integration_helpers.mock_success_tag_write_routes()


def _mock_article_note(response_json: dict[str, object] | None = None) -> respx.Route:
    return respx.post("https://zammad.example.local/api/v1/ticket_articles").mock(
        return_value=httpx.Response(
            200,
            json=response_json
            or {"id": 999, "internal": True, "subject": "ok", "body": "<p>ok</p>"},
        )
    )


def _mock_signing_failure_routes(
    *, articles: list[dict[str, object]] | None = None
) -> tuple[respx.Route, respx.Route, respx.Route]:
    _mock_signing_reads(articles=articles)
    remove_tag_route, add_tag_route = _mock_tag_writes()
    article_route = _mock_article_note({"id": 999})
    return remove_tag_route, add_tag_route, article_route


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


def _signing_payload(request_id: str) -> dict[str, object]:
    return {
        "ticket": {"id": 123},
        "_request_id": request_id,
        "user": {"login": "agent-from-webhook"},
    }


def _freeze_signing_now(monkeypatch: pytest.MonkeyPatch) -> datetime:
    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    freeze_process_ticket_now(monkeypatch, process_ticket_module, fixed_now)
    return fixed_now


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

    integration_helpers.assert_error_article_note(
        article_route,
        classification="Permanent",
        body_texts=("PKCS#12",),
    )


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

    integration_helpers.assert_error_article_note(
        article_route,
        classification="Transient",
        body_texts=() if expected_body_text is None else (expected_body_text,),
    )


def test_process_ticket_signing_writes_signed_pdf_and_audit_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pfx_path, expected_fingerprint = _write_signing_pfx(
        tmp_path,
        common_name="Integration Test Signer",
    )
    settings = _test_settings(str(tmp_path), pfx_path=pfx_path, password=fake_credential("secret"))

    fixed_now = _freeze_signing_now(monkeypatch)
    payload = _signing_payload("req-sign-1")

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
    tsa_url = "https://tsa.test/rfc3161"
    settings = _test_tsa_settings(tmp_path, tsa_url=tsa_url)

    _freeze_signing_now(monkeypatch)
    payload = _signing_payload("req-sign-tsa-err-1")

    with respx.mock:
        respx.post(tsa_url).mock(side_effect=httpx.ConnectError("boom"))
        remove_tag_route, add_tag_route, article_route = _mock_signing_failure_routes()

        asyncio.run(process_ticket("delivery-sign-tsa-err-1", payload, settings))

        _assert_transient_signing_error(
            remove_tag_route=remove_tag_route,
            add_tag_route=add_tag_route,
            article_route=article_route,
        )


def test_process_ticket_signing_with_invalid_pfx_password_is_permanent_and_drops_trigger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pfx_path, _ = _write_signing_pfx(tmp_path)
    settings = _test_settings(
        str(tmp_path), pfx_path=pfx_path, password=fake_credential("wrong-password")
    )

    _freeze_signing_now(monkeypatch)
    payload = _signing_payload("req-sign-bad-pass-1")

    with respx.mock:
        remove_tag_route, add_tag_route, article_route = _mock_signing_failure_routes(
            articles=[]
        )

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
    tsa_url = "https://tsa.test/rfc3161"
    settings = _test_tsa_settings(tmp_path, tsa_url=tsa_url)

    _freeze_signing_now(monkeypatch)
    payload = _signing_payload("req-sign-tsa-503-1")

    with respx.mock:
        respx.post(tsa_url).mock(return_value=httpx.Response(503))
        remove_tag_route, add_tag_route, article_route = _mock_signing_failure_routes(
            articles=[]
        )

        asyncio.run(process_ticket("delivery-sign-tsa-503-1", payload, settings))

        _assert_transient_signing_error(
            remove_tag_route=remove_tag_route,
            add_tag_route=add_tag_route,
            article_route=article_route,
            expected_body_text="HTTP 503",
        )
