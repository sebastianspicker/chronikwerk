from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from scripts.demo.seed_demo_http import history_status_counts


def history_count(history_payload: dict[str, Any]) -> int:
    try:
        return int(history_payload.get("count", 0))
    except (TypeError, ValueError):
        return 0


def write_seed_report(
    *,
    report_path: Path,
    report: dict[str, Any],
    failures: list[str],
    status_counts: dict[str, int],
) -> int:
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    if failures:
        print(f"Seed incomplete. Report written to {report_path}")
        print("Failures:")
        for failure in failures:
            print(f"- {failure}")
    else:
        print(f"Seed complete. Report written to {report_path}")
    print("History status counts:")
    print(json.dumps(status_counts, indent=2, sort_keys=True))
    return 1 if failures else 0


def seed_failures(
    *,
    ingests: list[dict[str, Any]],
    history_payload: dict[str, Any],
    target_count: int,
    backend_unavailable: dict[str, Any] | None,
) -> list[str]:
    failures: list[str] = []

    for ingest in ingests:
        if ingest["http_status"] != 202:
            failures.append(
                "ingest "
                f"ticket_id={ingest['ticket_id']} delivery_id={ingest['delivery_id']} "
                f"returned HTTP {ingest['http_status']}"
            )

    count = history_count(history_payload)
    if count < target_count:
        failures.append(f"history count {count} is below expected seed count {target_count}")

    if backend_unavailable is not None and not backend_unavailable.get("ok", False):
        failures.append(
            "backend-unavailable check expected HTTP "
            f"{backend_unavailable.get('expected_status_code')}, got "
            f"{backend_unavailable.get('status_code')}"
        )

    return failures


def build_seed_report(
    *,
    dataset_path: Path,
    evidence: Any,
) -> tuple[dict[str, Any], list[str], dict[str, int]]:
    status_counts = history_status_counts(evidence.history_payload)
    report = {
        "dataset": str(dataset_path),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ingest_requests": evidence.ingests,
        "history_status_counts": status_counts,
        "history": evidence.history_payload,
        "queue_stats": {
            "status_code": evidence.queue_code,
            "payload": evidence.queue_payload,
        },
        "mock_state": {
            "status_code": evidence.mock_state_code,
            "payload": evidence.mock_state_payload,
        },
        "mock_reset": {
            "status_code": evidence.reset_code,
            "payload": evidence.reset_payload,
        },
    }
    if evidence.backend_unavailable is not None:
        report["backend_unavailable_test"] = evidence.backend_unavailable

    failures = seed_failures(
        ingests=evidence.ingests,
        history_payload=evidence.history_payload,
        target_count=evidence.target_count,
        backend_unavailable=evidence.backend_unavailable,
    )
    report["status"] = "partial" if failures else "ok"
    report["failures"] = failures
    return report, failures, status_counts
