from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.e2e.docker_api_smoke_artifacts import assert_artifacts, expected_processed_ticket_ids
from scripts.e2e.docker_api_smoke_errors import PROCESSED_STATUS, E2EFailure

__all__ = [
    "E2EFailure",
    "PROCESSED_STATUS",
    "assert_artifacts",
    "assert_expected_statuses",
    "expected_processed_ticket_ids",
    "expected_statuses_from_dataset",
    "latest_status_by_ticket",
    "load_dataset",
]

def load_dataset(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise E2EFailure("dataset phase: dataset must be a JSON object")
    seed_plan = payload.get("seed_plan")
    if not isinstance(seed_plan, list) or not seed_plan:
        raise E2EFailure("dataset phase: dataset.seed_plan must be a non-empty list")
    return payload


def expected_statuses_from_dataset(dataset: dict[str, Any]) -> dict[int, str]:
    seed_plan = dataset.get("seed_plan")
    if not isinstance(seed_plan, list):
        raise E2EFailure("dataset phase: dataset.seed_plan must be a list")

    expected: dict[int, str] = {}
    for index, item in enumerate(seed_plan):
        if not isinstance(item, dict):
            raise E2EFailure(f"dataset phase: seed_plan[{index}] must be an object")
        try:
            ticket_id = int(item["ticket_id"])
            status = str(item["expected_status"]).strip()
        except (KeyError, TypeError, ValueError) as exc:
            raise E2EFailure(
                f"dataset phase: seed_plan[{index}] must define ticket_id and expected_status"
            ) from exc
        if not status:
            raise E2EFailure(f"dataset phase: seed_plan[{index}].expected_status is empty")
        expected[ticket_id] = status
    return expected


def latest_status_by_ticket(history_payload: dict[str, Any]) -> dict[int, str]:
    items = history_payload.get("items")
    if not isinstance(items, list):
        raise E2EFailure("history phase: admin history payload has no items list")

    statuses: dict[int, str] = {}
    # /admin/api/history returns newest first; keep the first status per ticket.
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_ticket_id = item.get("ticket_id")
        raw_status = item.get("status")
        if raw_ticket_id is None or raw_status is None:
            continue
        try:
            ticket_id = int(raw_ticket_id)
        except (TypeError, ValueError):
            continue
        status = str(raw_status)
        statuses.setdefault(ticket_id, status)
    return statuses


def assert_expected_statuses(
    history_payload: dict[str, Any],
    expected: dict[int, str],
) -> None:
    latest = latest_status_by_ticket(history_payload)
    mismatches: list[str] = []
    for ticket_id, expected_status in sorted(expected.items()):
        actual = latest.get(ticket_id)
        if actual != expected_status:
            mismatches.append(f"ticket {ticket_id}: expected {expected_status!r}, got {actual!r}")
    if mismatches:
        raise E2EFailure("history phase: terminal status mismatch: " + "; ".join(mismatches))
