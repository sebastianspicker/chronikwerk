from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx
from pydantic import ValidationError

from test.support.checks import check
from test.support.credentials import fake_credential
from test.support.integration_helpers import (
    expected_agent_archive_pdf_path,
    mock_success_zammad_write_routes,
    zammad_storage_settings,
)
from test.support.time_control import freeze_process_ticket_now
from zammad_pdf_archiver.app.jobs import process_ticket as process_ticket_module
from zammad_pdf_archiver.app.jobs.process_ticket import process_ticket
from zammad_pdf_archiver.config.settings import Settings


def _settings(storage_root: str, *, fsync: bool = True) -> Settings:
    return zammad_storage_settings(storage_root, storage_overrides={"fsync": fsync})


def _mock_happy_zammad(ticket_id: int = 123) -> None:
    respx.get(f"https://zammad.example.local/api/v1/tickets/{ticket_id}").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": ticket_id,
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
            },
        )
    )

    respx.get(
        "https://zammad.example.local/api/v1/tags",
        params={"object": "Ticket", "o_id": str(ticket_id)},
    ).mock(return_value=httpx.Response(200, json=["pdf:sign"]))

    respx.get(f"https://zammad.example.local/api/v1/ticket_articles/by_ticket/{ticket_id}").mock(
        return_value=httpx.Response(200, json=[])
    )

    mock_success_zammad_write_routes()


def _expected_pdf_path(
    tmp_path: Path,
    *,
    settings: Settings,
    ticket_number: str,
    fixed_now: datetime,
) -> Path:
    return expected_agent_archive_pdf_path(
        tmp_path,
        settings=settings,
        fixed_now=fixed_now,
        ticket_number=ticket_number,
    )


def test_storage_fsync_can_be_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(str(tmp_path), fsync=False)
    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    freeze_process_ticket_now(monkeypatch, process_ticket_module, fixed_now)

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
    check(not not expected_pdf.exists(), "assertion failed")


def test_storage_atomic_write_setting_is_not_supported(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="atomic_write"):
        Settings.from_mapping(
            {
                "zammad": {
                    "base_url": "https://zammad.example.local",
                    "api_token": fake_credential("test-token"),
                },
                "storage": {
                    "root": str(tmp_path),
                    "atomic_write": False,
                },
            }
        )
