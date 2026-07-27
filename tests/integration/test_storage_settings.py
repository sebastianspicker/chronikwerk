"""Verifies storage durability settings control filesystem synchronization."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from chronikwerk.adapters.storage.layout import build_filename_from_pattern
from chronikwerk.app.jobs import _ticket_pipeline as ticket_pipeline_module
from chronikwerk.app.jobs.process_ticket import process_ticket
from chronikwerk.config.settings import Settings
from tests.support.zammad_fixtures import archived_ticket_json, register_archive_mutation_routes


def _settings(storage_root: str, *, fsync: bool = True) -> Settings:
    """Build settings isolated to this test scenario."""
    return Settings.from_mapping(
        {
            "zammad": {"base_url": "https://zammad.example.local", "api_token": "test-token"},
            "storage": {"root": storage_root, "fsync": fsync},
            "hardening": {"transport": {"allow_private_networks": True}},
        }
    )


def _mock_happy_zammad(ticket_id: int = 123) -> None:
    """Stub successful Zammad traffic needed to reach filesystem storage."""
    respx.get(f"https://zammad.example.local/api/v1/tickets/{ticket_id}").mock(
        return_value=httpx.Response(
            200,
            json=archived_ticket_json(ticket_id=ticket_id),
        )
    )

    respx.get(
        "https://zammad.example.local/api/v1/tags",
        params={"object": "Ticket", "o_id": str(ticket_id)},
    ).mock(return_value=httpx.Response(200, json=["pdf:sign"]))

    respx.get(f"https://zammad.example.local/api/v1/ticket_articles/by_ticket/{ticket_id}").mock(
        return_value=httpx.Response(200, json=[])
    )

    register_archive_mutation_routes()


def _expected_pdf_path(
    tmp_path: Path,
    *,
    settings: Settings,
    ticket_number: str,
    fixed_now: datetime,
) -> Path:
    """Return the expected persisted PDF path for the scenario."""
    filename = build_filename_from_pattern(
        settings.storage.filename_pattern,
        ticket_number=ticket_number,
        timestamp_utc=fixed_now.date().isoformat(),
    )
    return tmp_path / "agent" / "A" / "B" / "C" / filename


def test_storage_fsync_can_be_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(str(tmp_path), fsync=False)
    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(ticket_pipeline_module, "now_utc", lambda: fixed_now)

    def _fsync(_: int) -> None:
        raise AssertionError("os.fsync must not be called when storage.fsync=false")

    monkeypatch.setattr(os, "fsync", _fsync)

    payload = {"ticket": {"id": 123}, "_request_id": "req-fsync-off-1"}
    with respx.mock:
        _mock_happy_zammad(ticket_id=123)
        asyncio.run(process_ticket("delivery-fsync-off-1", payload, settings))

    expected_pdf = _expected_pdf_path(
        tmp_path, settings=settings, ticket_number="20240123", fixed_now=fixed_now
    )
    assert expected_pdf.exists()
