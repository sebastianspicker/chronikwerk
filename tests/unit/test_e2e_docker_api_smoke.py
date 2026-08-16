"""Verifies Docker smoke-result parsing and artifact-status assertions."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from scripts.e2e import docker_api_smoke


def _run_args() -> argparse.Namespace:
    """Build the deterministic argument set shared by smoke-runner unit tests."""
    repo_root = Path(__file__).resolve().parents[2]
    return argparse.Namespace(
        project="chronikwerk-e2e",
        compose_file=repo_root / "infra/e2e/docker-compose.yml",
        dataset=repo_root / "infra/e2e/dataset.json",
        archiver_url="http://127.0.0.1:18080",
        mock_url="http://127.0.0.1:18090",
        admin_token="demo-admin-token",
        history_token="demo-history-token",
        hmac_secret="demo-hmac-secret",
        timeout_seconds=90.0,
        keep_stack=False,
        dry_run=False,
    )


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
        "entries": [
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
    payload = {"entries": [{"ticket_id": 1101, "status": "failed_permanent"}]}

    with pytest.raises(docker_api_smoke.E2EFailure, match="ticket 1101"):
        docker_api_smoke.assert_expected_statuses(payload, {1101: "processed"})


def test_assert_artifacts_accepts_pdf_and_sidecar_payload() -> None:
    payload = {
        "pdf_count": 2,
        "bad_pdfs": [],
        "artifacts": [
            {"ticket_id": 1101, "sha256": "a", "pdf_sha256": "a"},
            {"ticket_id": 1102, "sha256": "b", "pdf_sha256": "b"},
        ],
    }

    docker_api_smoke.assert_artifacts(payload, {1101, 1102})


def test_assert_artifacts_reports_missing_sidecar() -> None:
    payload = {
        "pdf_count": 2,
        "bad_pdfs": [],
        "artifacts": [{"ticket_id": 1101, "sha256": "a", "pdf_sha256": "a"}],
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


def test_assert_mock_state_accepts_signed_ticket_with_archive_note() -> None:
    docker_api_smoke.assert_mock_state(
        {
            "tags": {"1101": ["pdf:signed"]},
            "notes": {"1101": [{"subject": "PDF archived"}]},
        },
        {1101},
    )


def test_assert_mock_state_rejects_missing_archive_note() -> None:
    with pytest.raises(docker_api_smoke.E2EFailure, match="no archive note"):
        docker_api_smoke.assert_mock_state(
            {
                "tags": {"1101": ["pdf:signed"]},
                "notes": {"1101": []},
            },
            {1101},
        )


def test_dry_run_prints_docker_api_plan(capsys: pytest.CaptureFixture[str]) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    args = argparse.Namespace(
        project="chronikwerk-e2e",
        compose_file=repo_root / "infra/e2e/docker-compose.yml",
        dataset=repo_root / "infra/e2e/dataset.json",
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


def test_e2e_script_supports_dry_run_cli(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "scripts" / "e2e" / "docker_api_smoke.py"

    monkeypatch.setattr(sys, "argv", [str(script), "--dry-run"])

    assert docker_api_smoke.main() == 0
    out = capsys.readouterr().out
    assert "Docker API E2E smoke" in out
    assert "docker compose -p chronikwerk-e2e" in out


def test_busy_ports_do_not_mutate_an_unrelated_project(monkeypatch: pytest.MonkeyPatch) -> None:
    docker_commands: list[list[str]] = []

    def fail_preflight(*_args: object, **_kwargs: object) -> None:
        raise docker_api_smoke.E2EFailure("startup phase: required port already in use")

    def record_docker_command(args: Sequence[str], *, phase: str) -> str:
        docker_commands.append(list(args))
        assert phase == "startup"
        return ""

    monkeypatch.setattr(docker_api_smoke, "_assert_ports_available", fail_preflight)
    monkeypatch.setattr(docker_api_smoke, "_run_checked", record_docker_command)

    with pytest.raises(docker_api_smoke.E2EFailure, match="required port already in use"):
        docker_api_smoke._prepare_stack(  # noqa: SLF001
            _run_args(),
            _run_args().compose_file,
        )

    assert len(docker_commands) == 1
    assert docker_commands[0][-4:] == ["ps", "--status", "running", "--quiet"]


def test_busy_ports_from_retained_project_are_recovered(monkeypatch: pytest.MonkeyPatch) -> None:
    args = _run_args()
    port_checks = 0
    docker_commands: list[tuple[list[str], str]] = []

    def check_ports(*_args: object, **_kwargs: object) -> None:
        nonlocal port_checks
        port_checks += 1
        if port_checks == 1:
            raise docker_api_smoke.E2EFailure("startup phase: required port already in use")

    def run_checked(command: Sequence[str], *, phase: str) -> str:
        docker_commands.append((list(command), phase))
        return "retained-container-id\n" if "ps" in command else ""

    monkeypatch.setattr(docker_api_smoke, "_assert_ports_available", check_ports)
    monkeypatch.setattr(docker_api_smoke, "_run_checked", run_checked)

    assert docker_api_smoke._prepare_stack(args, args.compose_file)[-1] == str(args.compose_file)
    assert port_checks == 2
    assert docker_commands[0][0][-4:] == ["ps", "--status", "running", "--quiet"]
    assert docker_commands[1][0][-3:] == ["down", "-v", "--remove-orphans"]
    assert docker_commands[1][1] == "cleanup"


def test_cleanup_failure_fails_an_otherwise_successful_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _run_args()

    monkeypatch.setattr(
        docker_api_smoke,
        "_prepare_stack",
        lambda *_args: ["docker", "compose", "-p", args.project],
    )
    monkeypatch.setattr(docker_api_smoke, "_wait_for_stack_ready", lambda *_args: None)
    monkeypatch.setattr(
        docker_api_smoke,
        "_exercise_ingest_flow",
        lambda *_args, **_kwargs: {1101: docker_api_smoke.PROCESSED_STATUS},
    )
    monkeypatch.setattr(docker_api_smoke, "_verify_mock_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(docker_api_smoke, "_verify_artifacts", lambda *_args, **_kwargs: None)

    def run_checked(args: list[str], *, phase: str) -> str:
        if phase == "cleanup":
            raise docker_api_smoke.E2EFailure("cleanup phase: command failed: exit code 1")
        return ""

    monkeypatch.setattr(docker_api_smoke, "_run_checked", run_checked)

    with pytest.raises(docker_api_smoke.E2EFailure, match="cleanup phase"):
        docker_api_smoke.run(args)


def test_cleanup_failure_preserves_the_primary_failure(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    args = _run_args()
    primary_error = docker_api_smoke.E2EFailure("startup phase: primary failure")

    monkeypatch.setattr(
        docker_api_smoke,
        "_prepare_stack",
        lambda *_args: ["docker", "compose", "-p", args.project],
    )

    def run_checked(_args: list[str], *, phase: str) -> str:
        if phase == "startup":
            raise primary_error
        raise docker_api_smoke.E2EFailure("cleanup phase: command failed: exit code 1")

    monkeypatch.setattr(docker_api_smoke, "_run_checked", run_checked)

    with pytest.raises(docker_api_smoke.E2EFailure, match="primary failure") as raised:
        docker_api_smoke.run(args)

    assert raised.value is primary_error
    assert primary_error.__notes__ == [
        "cleanup failure: cleanup phase: command failed: exit code 1"
    ]
    assert "ERROR: cleanup failure: cleanup phase: command failed: exit code 1" in (
        capsys.readouterr().err
    )
