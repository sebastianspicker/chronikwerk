from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.e2e import docker_api_smoke


def test_expected_statuses_from_dataset() -> None:
    dataset = {
        "seed_plan": [
            {"ticket_id": 1101, "expected_status": "processed"},
            {"ticket_id": "1103", "expected_status": "failed_permanent"},
        ]
    }

    assert docker_api_smoke.expected_statuses_from_dataset(dataset) == {
        1101: "processed",
        1103: "failed_permanent",
    }


def test_expected_statuses_rejects_missing_status() -> None:
    dataset = {"seed_plan": [{"ticket_id": 1101}]}

    with pytest.raises(docker_api_smoke.E2EFailure, match="ticket_id and expected_status"):
        docker_api_smoke.expected_statuses_from_dataset(dataset)


def test_latest_status_by_ticket_keeps_newest_history_item() -> None:
    payload = {
        "items": [
            {"ticket_id": 1104, "status": "processed"},
            {"ticket_id": 1104, "status": "skipped_not_triggered"},
            {"ticket_id": 1101, "status": "processed"},
        ]
    }

    assert docker_api_smoke.latest_status_by_ticket(payload) == {
        1101: "processed",
        1104: "processed",
    }


def test_assert_expected_statuses_reports_mismatch() -> None:
    payload = {"items": [{"ticket_id": 1101, "status": "failed_permanent"}]}

    with pytest.raises(docker_api_smoke.E2EFailure, match="ticket 1101"):
        docker_api_smoke.assert_expected_statuses(payload, {1101: "processed"})


def test_assert_artifacts_accepts_pdf_and_sidecar_payload() -> None:
    payload = {
        "pdf_count": 2,
        "bad_pdfs": [],
        "sidecar_ticket_ids": [1101, "1102"],
    }

    docker_api_smoke.assert_artifacts(payload, {1101, 1102})


def test_assert_artifacts_reports_missing_sidecar() -> None:
    payload = {
        "pdf_count": 2,
        "bad_pdfs": [],
        "sidecar_ticket_ids": [1101],
    }

    with pytest.raises(docker_api_smoke.E2EFailure, match="missing sidecars"):
        docker_api_smoke.assert_artifacts(payload, {1101, 1102})


def test_expected_processed_ticket_ids_derives_from_status_map() -> None:
    assert docker_api_smoke.expected_processed_ticket_ids(
        {
            1101: "processed",
            1103: "failed_permanent",
            1104: "processed",
        }
    ) == {1101, 1104}


def test_dry_run_prints_docker_api_plan(capsys: pytest.CaptureFixture[str]) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    args = argparse.Namespace(
        project="zammad-archiver-e2e",
        compose_file=repo_root / "docker-compose.yml",
        dataset=repo_root / "examples/demo/mock_university_dataset.json",
        archiver_url="http://127.0.0.1:18080",
        mock_url="http://127.0.0.1:18090",
        admin_token="demo-admin-token",
        timeout_seconds=90.0,
        keep_stack=False,
        dry_run=True,
    )

    assert docker_api_smoke.run(args) == 0
    out = capsys.readouterr().out
    assert "DRY RUN: Docker API E2E smoke" in out
    assert "POST /ingest ticket_id=1101 expected=processed" in out
    assert "POST /retry/1104 expected=processed" in out


def test_e2e_script_supports_dry_run_subprocess() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "scripts" / "e2e" / "docker_api_smoke.py"

    proc = subprocess.run(
        [sys.executable, str(script), "--dry-run"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert "Docker API E2E smoke" in proc.stdout
    assert "docker compose -p zammad-archiver-e2e" in proc.stdout
