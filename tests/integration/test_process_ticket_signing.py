"""Exercises signed-PDF processing and TSA failure classification."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from chronikwerk._version import VERSION
from chronikwerk.adapters.storage.layout import build_filename_from_pattern
from chronikwerk.app.jobs import _ticket_pipeline as ticket_pipeline_module
from chronikwerk.app.jobs import (
    _ticket_pipeline_errors as ticket_pipeline_errors_module,
)
from chronikwerk.app.jobs.process_ticket import process_ticket
from chronikwerk.config.settings import Settings
from tests.support.process_ticket_helpers import assert_artifact_pair_exists
from tests.support.signing_test_helpers import write_test_pfx
from tests.support.zammad_fixtures import (
    html_article_json,
    register_archived_ticket_fetch_routes,
)

pytest.importorskip("pyhanko", reason="Signing integration requires pyHanko")


def _test_settings(storage_root: str, *, pfx_path: Path, password: str) -> Settings:
    """Build settings isolated to this test scenario."""
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
    """Build signing settings that force a timestamp-authority outage."""
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
    """Return a deterministic clock value for timestamp-sensitive assertions."""
    fixed = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(ticket_pipeline_module, "now_utc", lambda: fixed)
    monkeypatch.setattr(ticket_pipeline_errors_module, "now_utc", lambda: fixed)
    return fixed


def _payload(request_id: str) -> dict[str, object]:
    """Return a representative signed webhook payload for this scenario."""
    return {
        "ticket": {"id": 123},
        "_request_id": request_id,
        "user": {"login": "agent-from-webhook"},
    }


def _register_fetch_routes(*, articles: list[dict[str, object]]) -> None:
    """Stub ticket, trigger-tag, and article reads for signing scenarios."""
    register_archived_ticket_fetch_routes(articles=articles)


def _register_tag_routes() -> tuple[respx.Route, respx.Route]:
    """Stub tag mutations and retain routes for transition assertions."""
    remove_tag_route = respx.post("https://zammad.example.local/api/v1/tags/remove").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    add_tag_route = respx.post("https://zammad.example.local/api/v1/tags/add").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    return remove_tag_route, add_tag_route


def _register_article_route(json_body: dict[str, object] | None = None) -> respx.Route:
    """Stub internal-note creation with an optional response payload."""
    return respx.post("https://zammad.example.local/api/v1/ticket_articles").mock(
        return_value=httpx.Response(200, json=json_body or {"id": 999})
    )


def _called_items(route: respx.Route) -> set[str]:
    """Capture mocked Zammad mutation payloads for assertion."""
    return {json.loads(call.request.content.decode("utf-8"))["item"] for call in route.calls}


def _expected_pdf_path(tmp_path: Path, settings: Settings, fixed_now: datetime) -> Path:
    """Return the expected persisted PDF path for the scenario."""
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
    expected_fingerprint = write_test_pfx(pfx_path, password="secret")
    settings = _test_settings(str(tmp_path), pfx_path=pfx_path, password="secret")
    fixed_now = _fixed_now(monkeypatch)
    payload = _payload("req-sign-1")

    with respx.mock:
        _register_fetch_routes(articles=[html_article_json()])
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

        assert_artifact_pair_exists(expected_pdf_path, expected_sidecar_path)

        pdf_bytes = expected_pdf_path.read_bytes()
        assert pdf_bytes.startswith(b"%PDF")
        assert b"/ByteRange" in pdf_bytes

        audit = json.loads(expected_sidecar_path.read_text("utf-8"))
        assert audit["signing"]["enabled"] is True
        assert audit["signing"]["tsa_used"] is False
        assert audit["signing"]["cert_fingerprint"] == expected_fingerprint


def _register_ok_article_route() -> respx.Route:
    """Stub successful internal-note creation for the happy signing path."""
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
    """Assert the internal note classifies and explains the signing failure."""
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
    """Assert transient signing failure preserves the trigger for retry."""
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
    write_test_pfx(pfx_path, password="secret")
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
        _register_fetch_routes(articles=[html_article_json()])
        remove_tag_route, add_tag_route = _register_tag_routes()
        article_route = _register_ok_article_route()

        asyncio.run(process_ticket("delivery-sign-tsa-err-1", payload, settings))
        _assert_transient_signing_error(remove_tag_route, add_tag_route, article_route)


def test_process_ticket_signing_with_invalid_pfx_password_is_permanent_and_drops_trigger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pfx_path = tmp_path / "test.pfx"
    write_test_pfx(pfx_path, password="secret")
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
    write_test_pfx(pfx_path, password="secret")
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
