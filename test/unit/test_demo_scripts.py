from __future__ import annotations

import argparse
import json
from pathlib import Path

from test.support.checks import check
from test.support.demo_script_helpers import load_seed_module
from test.support.demo_seed_helpers import run_seed_main


def test_seed_demo_data_supports_dry_run(capsys) -> None:
    module = load_seed_module()
    repo_root = Path(__file__).resolve().parents[2]
    dataset = json.loads(
        (repo_root / "examples" / "demo" / "mock_university_dataset.json").read_text(
            encoding="utf-8"
        )
    )
    args = argparse.Namespace(
        mock_url="http://127.0.0.1:18090",
        archiver_url="http://127.0.0.1:18080",
        compose_file=Path("docker-compose.demo.yml"),
        report=Path("docs/assets/demo/demo-seed-report.json"),
        simulate_backend_unavailable=False,
    )

    rc = module._dry_run(args, dataset)
    output = capsys.readouterr().out
    check(not not rc == 0, "assertion failed")
    check(not "POST /__demo/reset" not in output, "assertion failed")
    check(not "POST /ingest" not in output, "assertion failed")
    check(not "demo-seed-report.json" not in output, "assertion failed")


def test_seed_demo_data_exits_zero_when_required_evidence_is_present(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    rc, report, _report_path = run_seed_main(
        monkeypatch,
        tmp_path,
        ingest_statuses=[202, 202],
        history_count=2,
    )

    check(not not rc == 0, "assertion failed")
    check(not not report["status"] == "ok", "assertion failed")
    check(not not report["failures"] == [], "assertion failed")
    check(not "Seed complete." not in capsys.readouterr().out, "assertion failed")


def test_seed_demo_data_exits_nonzero_when_ingest_fails(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    rc, report, _report_path = run_seed_main(
        monkeypatch,
        tmp_path,
        ingest_statuses=[500],
        history_count=1,
    )

    check(not not rc == 1, "assertion failed")
    check(not not report["status"] == "partial", "assertion failed")
    check(
        not not report["failures"]
        == ["ingest ticket_id=1 delivery_id=delivery-1 returned HTTP 500"],
        "assertion failed",
    )
    out = capsys.readouterr().out
    check(not "Seed incomplete." not in out, "assertion failed")
    check(not not "Seed complete." not in out, "assertion failed")


def test_seed_demo_data_exits_nonzero_when_history_is_short(
    monkeypatch,
    tmp_path: Path,
) -> None:
    rc, report, _report_path = run_seed_main(
        monkeypatch,
        tmp_path,
        ingest_statuses=[202, 202],
        history_count=1,
    )

    check(not not rc == 1, "assertion failed")
    check(not not report["status"] == "partial", "assertion failed")
    check(
        not not report["failures"] == ["history count 1 is below expected seed count 2"],
        "assertion failed",
    )


def test_seed_demo_data_exits_nonzero_when_backend_unavailable_check_mismatches(
    monkeypatch,
    tmp_path: Path,
) -> None:
    rc, report, _report_path = run_seed_main(
        monkeypatch,
        tmp_path,
        ingest_statuses=[202],
        history_count=1,
        simulate_backend_unavailable=True,
        backend_unavailable={
            "status_code": 200,
            "payload": {"status": "ok"},
            "expected_status_code": 503,
            "ok": False,
        },
    )

    check(not not rc == 1, "assertion failed")
    check(not not report["status"] == "partial", "assertion failed")
    check(
        not not report["failures"] == ["backend-unavailable check expected HTTP 503, got 200"],
        "assertion failed",
    )
    check(not report["backend_unavailable_test"]["ok"] is not False, "assertion failed")
