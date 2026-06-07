"""Seed-demo test doubles and runner helpers."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from test.support.credentials import fake_credential
from test.support.demo_script_helpers import load_seed_module
from test.support.demo_seed_client import FakeDemoClient


def write_seed_dataset(tmp_path: Path, count: int = 1) -> Path:
    seed_plan = [
        {
            "ticket_id": ticket_id,
            "delivery_id": f"delivery-{ticket_id}",
            "user_login": "demo.agent",
            "expected_status": "processed",
        }
        for ticket_id in range(1, count + 1)
    ]
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps({"seed_plan": seed_plan}), encoding="utf-8")
    return path


def seed_args(
    *,
    dataset: Path,
    report: Path,
    simulate_backend_unavailable: bool = False,
) -> argparse.Namespace:
    return argparse.Namespace(
        archiver_url="http://archiver.test",
        mock_url="http://mock.test",
        dataset=dataset,
        report=report,
        admin_token=fake_credential("admin-token"),
        compose_file=Path("docker-compose.demo.yml"),
        simulate_backend_unavailable=simulate_backend_unavailable,
        dry_run=False,
    )


def run_seed_main(
    monkeypatch,
    tmp_path: Path,
    *,
    ingest_statuses: list[int],
    history_count: int,
    simulate_backend_unavailable: bool = False,
    backend_unavailable: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any], str]:
    module = load_seed_module()
    dataset = write_seed_dataset(tmp_path, count=len(ingest_statuses))
    report = tmp_path / "report.json"
    fake_client = FakeDemoClient(
        ingest_statuses=list(ingest_statuses),
        history_count=history_count,
    )

    monkeypatch.setattr(
        module,
        "_parse_args",
        lambda: seed_args(
            dataset=dataset,
            report=report,
            simulate_backend_unavailable=simulate_backend_unavailable,
        ),
    )
    monkeypatch.setattr(module, "_wait_for_ready", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(module.httpx, "Client", lambda timeout: fake_client)
    if backend_unavailable is not None:
        monkeypatch.setattr(
            module,
            "_simulate_backend_unavailable",
            lambda **kwargs: backend_unavailable,
        )

    rc = module.main()
    return rc, json.loads(report.read_text("utf-8")), str(report)
