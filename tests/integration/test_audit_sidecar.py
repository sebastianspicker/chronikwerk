"""Verifies audit sidecars are written beside PDFs with matching checksums."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import respx

from chronikwerk.adapters.storage.layout import build_filename_from_pattern
from chronikwerk.app.jobs import _ticket_pipeline as ticket_pipeline_module
from chronikwerk.app.jobs.process_ticket import process_ticket
from chronikwerk.config.settings import Settings
from tests.support.zammad_fixtures import (
    html_article_json,
    register_archived_ticket_fetch_routes,
)


def _test_settings(storage_root: str) -> Settings:
    """Build settings isolated to this test scenario."""
    return Settings.from_mapping(
        {
            "zammad": {"base_url": "https://zammad.example.local", "api_token": "test-token"},
            "storage": {"root": storage_root},
            "hardening": {"transport": {"allow_private_networks": True}},
        }
    )


def _fixed_now(monkeypatch) -> datetime:
    """Return a deterministic clock value for timestamp-sensitive assertions."""
    fixed = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(ticket_pipeline_module, "now_utc", lambda: fixed)
    return fixed


def _payload() -> dict[str, object]:
    """Return a representative signed webhook payload for this scenario."""
    return {
        "ticket": {"id": 123},
        "_request_id": "req-audit-1",
        "user": {"login": "agent-from-webhook"},
    }


def _register_process_routes() -> respx.Route:
    """Stub successful Zammad reads, tag changes, and archive-note creation."""
    register_archived_ticket_fetch_routes(articles=[html_article_json()])
    respx.post("https://zammad.example.local/api/v1/tags/remove").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    respx.post("https://zammad.example.local/api/v1/tags/add").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    return respx.post("https://zammad.example.local/api/v1/ticket_articles").mock(
        return_value=httpx.Response(
            200,
            json={"id": 999, "internal": True, "subject": "ok", "body": "<p>ok</p>"},
        )
    )


def _expected_pdf_path(tmp_path, settings: Settings, fixed_now: datetime) -> Path:
    """Return the expected persisted PDF path for the scenario."""
    expected_filename = build_filename_from_pattern(
        settings.storage.filename_pattern,
        ticket_number="20240123",
        timestamp_utc=fixed_now.date().isoformat(),
    )
    return tmp_path / "agent" / "A" / "B" / "C" / expected_filename


def test_audit_sidecar_written_next_to_pdf_and_matches_sha256(tmp_path, monkeypatch) -> None:
    settings = _test_settings(str(tmp_path))
    fixed_now = _fixed_now(monkeypatch)
    payload = _payload()

    with respx.mock:
        article_route = _register_process_routes()

        asyncio.run(process_ticket("delivery-audit-1", payload, settings))

        expected_pdf_path = _expected_pdf_path(tmp_path, settings, fixed_now)
        expected_sidecar_path = expected_pdf_path.parent / (expected_pdf_path.name + ".json")

        assert expected_pdf_path.exists()
        assert expected_sidecar_path.exists()

        pdf_bytes = expected_pdf_path.read_bytes()
        sha256_hex = hashlib.sha256(pdf_bytes).hexdigest()

        audit = json.loads(expected_sidecar_path.read_text("utf-8"))
        assert audit["sha256"] == sha256_hex
        assert audit["storage_path"] == str(expected_pdf_path)
        assert audit["ticket_id"] == 123
        assert audit["ticket_number"] == "20240123"

        assert article_route.called
        posted = json.loads(article_route.calls[0].request.content.decode("utf-8"))
        assert sha256_hex in posted["body"]
        assert str(expected_sidecar_path) in posted["body"]
