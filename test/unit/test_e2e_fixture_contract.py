from __future__ import annotations

# Directly verifies the private fixture data used by the E2E contract.
# pylint: disable=protected-access
# ruff: noqa: I001  # Pylint and Ruff classify the in-repository test package differently.

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


def test_mock_tickets_match_current_path_resolution_contract(tmp_path: Path) -> None:
    module = _fixture_module()
    settings = Settings.from_mapping(
        {
            "zammad": {"base_url": "https://example.invalid", "api_token": "x"},
            "storage": {"root": str(tmp_path / "archive")},
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


def test_e2e_compose_mounts_ephemeral_signing_material() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    compose = (repo_root / "infra" / "e2e" / "docker-compose.yml").read_text(encoding="utf-8")

    assert 'SIGNING__ENABLED: "true"' in compose
    assert "SIGNING__PFX_PATH: /run/e2e-signing/e2e-signing.pfx" in compose
    assert "ZTA_E2E_PFX_PASSWORD" in compose
    assert "ZTA_E2E_SIGNING_DIR" in compose
    assert ":/run/e2e-signing:ro" in compose


def test_e2e_compose_initializes_archive_volume_for_non_root_runtime() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    compose = (repo_root / "infra" / "e2e" / "docker-compose.yml").read_text(encoding="utf-8")

    assert 'command: ["chown", "10001:10001", "/tmp/archive"]' in compose
    assert "condition: service_completed_successfully" in compose
