from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from scripts.e2e import docker_api_smoke
from test.support.checks import check
from test.support.credentials import fake_credential


def test_expected_statuses_from_dataset() -> None:
    dataset = {
        "seed_plan": [
            {"ticket_id": 1101, "expected_status": "processed"},
            {"ticket_id": "1103", "expected_status": "failed_permanent"},
        ]
    }

    check(
        not not docker_api_smoke.expected_statuses_from_dataset(dataset)
        == {1101: "processed", 1103: "failed_permanent"},
        "assertion failed",
    )


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

    check(
        not not docker_api_smoke.latest_status_by_ticket(payload)
        == {1101: "processed", 1104: "processed"},
        "assertion failed",
    )


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
    check(
        not not docker_api_smoke.expected_processed_ticket_ids(
            {1101: "processed", 1103: "failed_permanent", 1104: "processed"}
        )
        == {1101, 1104},
        "assertion failed",
    )


def test_dry_run_prints_docker_api_plan(capsys: pytest.CaptureFixture[str]) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    args = argparse.Namespace(
        project="zammad-archiver-e2e",
        compose_file=repo_root / "docker-compose.demo.yml",
        dataset=repo_root / "examples/demo/mock_university_dataset.json",
        archiver_url="http://127.0.0.1:18080",
        mock_url="http://127.0.0.1:18090",
        admin_token=fake_credential("demo-admin-token"),
        redis_port=16379,
        timeout_seconds=90.0,
        keep_stack=False,
        dry_run=True,
    )

    check(not not docker_api_smoke.run(args) == 0, "assertion failed")
    out = capsys.readouterr().out
    check(not "DRY RUN: Docker API E2E smoke" not in out, "assertion failed")
    check(not "POST /ingest ticket_id=1101 expected=processed" not in out, "assertion failed")
    check(not "POST /admin/api/retry/1104 expected=processed" not in out, "assertion failed")
    check(not "127.0.0.1:16379" not in out, "assertion failed")


def test_compose_base_uses_resolved_docker_and_existing_compose_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    compose_file = tmp_path / "compose.yml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    monkeypatch.setattr(docker_api_smoke.shutil, "which", lambda name: f"/usr/bin/{name}")

    check(
        not not docker_api_smoke._compose_base("proj", compose_file)
        == ["docker", "compose", "-p", "proj", "-f", str(compose_file.resolve())],
        "assertion failed",
    )


def test_compose_base_rejects_missing_docker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    compose_file = tmp_path / "compose.yml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    monkeypatch.setattr(docker_api_smoke.shutil, "which", lambda _name: None)

    with pytest.raises(docker_api_smoke.E2EFailure, match="docker executable not found"):
        docker_api_smoke._compose_base("proj", compose_file)  # noqa: SLF001


def test_e2e_script_supports_dry_run(capsys) -> None:
    dataset = {
        "seed_plan": [
            {"ticket_id": 1101, "expected_status": docker_api_smoke.PROCESSED_STATUS},
        ]
    }
    args = argparse.Namespace(
        project="zammad-archiver-e2e",
        compose_file=Path("docker-compose.demo.yml"),
        mock_url="http://127.0.0.1:18090",
        archiver_url="http://127.0.0.1:18080",
        redis_port=16379,
        keep_stack=False,
    )

    rc = docker_api_smoke._print_dry_run(args, dataset)  # noqa: SLF001
    output = capsys.readouterr().out
    check(not not rc == 0, "assertion failed")
    check(not "Docker API E2E smoke" not in output, "assertion failed")
    check(not "docker compose -p zammad-archiver-e2e" not in output, "assertion failed")
