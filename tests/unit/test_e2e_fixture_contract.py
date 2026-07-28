"""Verifies the end-to-end mock data matches current ticket-path semantics."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from chronikwerk.adapters.zammad.models import Ticket
from chronikwerk.app.jobs.ticket_path import determine_username, parse_archive_path_segments
from chronikwerk.config.settings import Settings


def _fixture_module() -> Any:
    """Load the shared end-to-end fixture module for public-contract checks."""
    path = Path(__file__).resolve().parents[2] / "infra/e2e/mock_zammad.py"
    spec = importlib.util.spec_from_file_location("e2e_mock_zammad", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_mock_tickets_match_current_path_resolution_contract() -> None:
    module = _fixture_module()
    settings = Settings.from_mapping(
        {
            "zammad": {"base_url": "https://example.invalid", "api_token": "x"},
            "storage": {"root": "/tmp/archive"},
        }
    )

    for raw_ticket in module._tickets.values():
        ticket = Ticket.model_validate(raw_ticket)
        custom_fields = ticket.preferences.custom_fields if ticket.preferences else {}
        fields = custom_fields or {}
        assert (
            determine_username(
                ticket=ticket,
                payload={"user": {"login": "e2e.agent"}},
                custom_fields=fields,
                mode_field_name=settings.fields.archive_user_mode,
                archive_user_field_name=settings.fields.archive_user,
            )
            == "e2e.owner"
        )
        assert parse_archive_path_segments(fields[settings.fields.archive_path]) == ["e2e"]


def test_mock_tag_lookup_preserves_zammad_query_names() -> None:
    module = _fixture_module()
    client = TestClient(module.app)

    response = client.get("/api/v1/tags", params={"object": "Ticket", "o_id": "1101"})

    assert response.status_code == 200
    assert response.json() == ["pdf:sign"]
