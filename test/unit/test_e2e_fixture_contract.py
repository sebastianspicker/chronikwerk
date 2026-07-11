from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from zammad_pdf_archiver.adapters.zammad.models import Ticket
from zammad_pdf_archiver.app.jobs.ticket_path import determine_username, parse_archive_path_segments
from zammad_pdf_archiver.config.settings import Settings


def _fixture_module() -> Any:
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
        assert determine_username(
            ticket=ticket,
            payload={"user": {"login": "e2e.agent"}},
            custom_fields=fields,
            mode_field_name=settings.fields.archive_user_mode,
            archive_user_field_name=settings.fields.archive_user,
        ) == "e2e.owner"
        assert parse_archive_path_segments(fields[settings.fields.archive_path]) == ["e2e"]
