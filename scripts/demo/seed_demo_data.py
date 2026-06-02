from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import time
from collections import Counter
from pathlib import Path
from typing import Any, NamedTuple

import httpx

DEFAULT_DATASET = Path("examples/demo/mock_university_dataset.json")
DEFAULT_REPORT = Path("docs/assets/demo/demo-seed-report.json")
DEFAULT_ARCHIVER_URL = "http://127.0.0.1:18080"
DEFAULT_MOCK_URL = "http://127.0.0.1:18090"
ADMIN_AUTH_ENV = "ZAMMAD_ARCHIVER_DEMO_ADMIN_TOKEN"
DEFAULT_COMPOSE_FILE = Path("docker-compose.demo.yml")


class _CommandResult(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


class _SeedRunEvidence(NamedTuple):
    ingests: list[dict[str, Any]]
    history_payload: dict[str, Any]
    queue_code: int
    queue_payload: Any
    mock_state_code: int
    mock_state_payload: Any
    reset_code: int
    reset_payload: Any
    backend_unavailable: dict[str, Any] | None
    target_count: int


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed deterministic demo data into local demo stack"
    )
    parser.add_argument("--archiver-url", default=DEFAULT_ARCHIVER_URL)
    parser.add_argument("--mock-url", default=DEFAULT_MOCK_URL)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--admin-token", default=os.environ.get(ADMIN_AUTH_ENV))
    parser.add_argument("--compose-file", type=Path, default=DEFAULT_COMPOSE_FILE)
    parser.add_argument(
        "--simulate-backend-unavailable",
        action="store_true",
        help="Temporarily stop redis-demo and verify admin API returns 503",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def _load_dataset(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("dataset must be a JSON object")
    seed_plan = payload.get("seed_plan")
    if not isinstance(seed_plan, list) or not seed_plan:
        raise ValueError("dataset.seed_plan must be a non-empty list")
    return payload


def _wait_for_ready(client: httpx.Client, label: str, url: str, *, timeout_s: float = 60.0) -> None:
    deadline = time.monotonic() + timeout_s
    last_error = ""
    while time.monotonic() < deadline:
        try:
            response = client.get(url)
            if response.status_code == 200:
                return
            last_error = f"HTTP {response.status_code}"
        except Exception as exc:  # pragma: no cover - defensive
            last_error = str(exc)
        time.sleep(1.0)
    raise RuntimeError(f"{label} not ready at {url}: {last_error}")


def _request_json(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: Any | None = None,
) -> tuple[int, Any]:
    response = client.request(method, url, headers=headers, json=json_body)
    text = response.text
    try:
        parsed: Any = response.json()
    except Exception:
        parsed = {"raw": text}
    return response.status_code, parsed


async def _run_compose_exec(
    compose_file: Path, args: tuple[str, ...], *, executable: str
) -> _CommandResult:
    proc = await asyncio.create_subprocess_exec(
        "docker",
        "compose",
        "-f",
        str(compose_file),
        *args,
        executable=executable,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return _CommandResult(
        returncode=int(proc.returncode or 0),
        stdout=stdout.decode(),
        stderr=stderr.decode(),
    )


def _compose(compose_file: Path, *args: str) -> _CommandResult:
    docker = shutil.which("docker")
    if docker is None:
        raise RuntimeError("docker executable not found on PATH")
    compose_path = compose_file.expanduser()
    if not compose_path.is_file():
        raise RuntimeError(f"compose file not found: {compose_file}")
    return asyncio.run(
        _run_compose_exec(compose_path.resolve(), args, executable=docker)
    )


def _dry_run(args: argparse.Namespace, dataset: dict[str, Any]) -> int:
    seed_plan = dataset["seed_plan"]
    print("DRY RUN: demo seed actions")
    print(f"- Wait for: GET {args.mock_url}/healthz")
    print(f"- Wait for: GET {args.archiver_url}/healthz")
    print(f"- POST /__demo/reset -> {args.mock_url}/__demo/reset")
    for item in seed_plan:
        print(
            "- POST /ingest "
            f"ticket_id={item.get('ticket_id')} delivery_id={item.get('delivery_id')} "
            f"expected={item.get('expected_status')}"
        )
    if args.simulate_backend_unavailable:
        print(f"- docker compose -f {args.compose_file} stop redis-demo")
        print(f"- GET /admin/api/history (expect 503) -> {args.archiver_url}/admin/api/history")
        print(f"- docker compose -f {args.compose_file} start redis-demo")
    print(f"- Write report: {args.report}")
    return 0


def _simulate_backend_unavailable(
    *,
    client: httpx.Client,
    archiver_url: str,
    admin_token: str,
    compose_file: Path,
) -> dict[str, Any]:
    stop = _compose(compose_file, "stop", "redis-demo")
    if stop.returncode != 0:
        raise RuntimeError(f"failed to stop redis-demo: {stop.stderr.strip()}")

    status_code, payload = _request_json(
        client,
        "GET",
        f"{archiver_url}/admin/api/history?limit=10",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    start = _compose(compose_file, "start", "redis-demo")
    if start.returncode != 0:
        raise RuntimeError(f"failed to start redis-demo: {start.stderr.strip()}")

    _wait_for_ready(client, "archiver", f"{archiver_url}/healthz", timeout_s=45.0)

    return {
        "status_code": status_code,
        "payload": payload,
        "expected_status_code": 503,
        "ok": status_code == 503,
    }


def _history_count(history_payload: dict[str, Any]) -> int:
    try:
        return int(history_payload.get("count", 0))
    except (TypeError, ValueError):
        return 0


def _auth_header(admin_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_token}"}


def _submit_seed_ingests(
    client: httpx.Client,
    *,
    archiver_url: str,
    seed_plan: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    ingests: list[dict[str, Any]] = []
    for item in seed_plan:
        ticket_id = int(item["ticket_id"])
        delivery_id = str(item.get("delivery_id") or f"demo-delivery-{ticket_id}")
        user_login = str(item.get("user_login") or "demo.agent")
        expected_status = str(item.get("expected_status") or "unknown")

        status_code, payload = _request_json(
            client,
            "POST",
            f"{archiver_url}/ingest",
            headers={"X-Zammad-Delivery": delivery_id},
            json_body={"ticket": {"id": ticket_id}, "user": {"login": user_login}},
        )
        ingests.append(
            {
                "ticket_id": ticket_id,
                "delivery_id": delivery_id,
                "expected_status": expected_status,
                "http_status": status_code,
                "response": payload,
            }
        )
    return ingests


def _poll_history(
    client: httpx.Client,
    *,
    archiver_url: str,
    admin_token: str,
    target_count: int,
) -> dict[str, Any]:
    history_payload: dict[str, Any] = {}
    for _ in range(30):
        history_code, data = _request_json(
            client,
            "GET",
            f"{archiver_url}/admin/api/history?limit=200",
            headers=_auth_header(admin_token),
        )
        if history_code == 200 and isinstance(data, dict):
            history_payload = data
            if int(data.get("count", 0)) >= target_count:
                break
        time.sleep(1.0)
    return history_payload


def _history_status_counts(history_payload: dict[str, Any]) -> dict[str, int]:
    raw_items = history_payload.get("items") if isinstance(history_payload, dict) else []
    items = raw_items if isinstance(raw_items, list) else []
    return dict(
        Counter(str(item.get("status", "unknown")) for item in items if isinstance(item, dict))
    )


def _write_seed_report(
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


def _seed_failures(
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

    count = _history_count(history_payload)
    if count < target_count:
        failures.append(f"history count {count} is below expected seed count {target_count}")

    if backend_unavailable is not None and not backend_unavailable.get("ok", False):
        failures.append(
            "backend-unavailable check expected HTTP "
            f"{backend_unavailable.get('expected_status_code')}, got "
            f"{backend_unavailable.get('status_code')}"
        )

    return failures


def _reset_mock_zammad(client: httpx.Client, mock_url: str) -> tuple[int, Any]:
    reset_code, reset_payload = _request_json(
        client,
        "POST",
        f"{mock_url}/__demo/reset",
    )
    if reset_code != 200:
        raise RuntimeError(f"mock reset failed ({reset_code}): {reset_payload}")
    return reset_code, reset_payload


def _fetch_queue_and_mock_state(
    client: httpx.Client,
    *,
    archiver_url: str,
    mock_url: str,
    admin_token: str,
) -> tuple[int, Any, int, Any]:
    queue_code, queue_payload = _request_json(
        client,
        "GET",
        f"{archiver_url}/admin/api/queue/stats",
        headers=_auth_header(admin_token),
    )
    mock_state_code, mock_state_payload = _request_json(
        client,
        "GET",
        f"{mock_url}/__demo/state",
    )
    return queue_code, queue_payload, mock_state_code, mock_state_payload


def _maybe_simulate_backend_unavailable(
    client: httpx.Client,
    *,
    args: argparse.Namespace,
) -> dict[str, Any] | None:
    if not args.simulate_backend_unavailable:
        return None
    return _simulate_backend_unavailable(
        client=client,
        archiver_url=args.archiver_url,
        admin_token=args.admin_token,
        compose_file=args.compose_file,
    )


def _collect_seed_evidence(
    *,
    args: argparse.Namespace,
    seed_plan: list[dict[str, Any]],
) -> _SeedRunEvidence:
    with httpx.Client(timeout=20.0) as client:
        _wait_for_ready(client, "mock-zammad", f"{args.mock_url}/healthz")
        _wait_for_ready(client, "archiver", f"{args.archiver_url}/healthz")

        reset_code, reset_payload = _reset_mock_zammad(client, args.mock_url)
        ingests = _submit_seed_ingests(
            client,
            archiver_url=args.archiver_url,
            seed_plan=seed_plan,
        )

        # Queue worker is async; wait until we see at least one history event per seed action.
        target_count = len(seed_plan)
        history_payload = _poll_history(
            client,
            archiver_url=args.archiver_url,
            admin_token=args.admin_token,
            target_count=target_count,
        )

        queue_code, queue_payload, mock_state_code, mock_state_payload = (
            _fetch_queue_and_mock_state(
                client,
                archiver_url=args.archiver_url,
                mock_url=args.mock_url,
                admin_token=args.admin_token,
            )
        )
        backend_unavailable = _maybe_simulate_backend_unavailable(client, args=args)

    return _SeedRunEvidence(
        ingests=ingests,
        history_payload=history_payload,
        queue_code=queue_code,
        queue_payload=queue_payload,
        mock_state_code=mock_state_code,
        mock_state_payload=mock_state_payload,
        reset_code=reset_code,
        reset_payload=reset_payload,
        backend_unavailable=backend_unavailable,
        target_count=target_count,
    )


def _build_seed_report(
    *,
    dataset_path: Path,
    evidence: _SeedRunEvidence,
) -> tuple[dict[str, Any], list[str], dict[str, int]]:
    status_counts = _history_status_counts(evidence.history_payload)
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

    failures = _seed_failures(
        ingests=evidence.ingests,
        history_payload=evidence.history_payload,
        target_count=evidence.target_count,
        backend_unavailable=evidence.backend_unavailable,
    )
    report["status"] = "partial" if failures else "ok"
    report["failures"] = failures
    return report, failures, status_counts


def main() -> int:
    args = _parse_args()
    dataset_path = args.dataset.expanduser().resolve()
    dataset = _load_dataset(dataset_path)

    if args.dry_run:
        return _dry_run(args, dataset)
    if not args.admin_token:
        raise RuntimeError(f"set --admin-token or {ADMIN_AUTH_ENV}")

    seed_plan: list[dict[str, Any]] = dataset["seed_plan"]
    report_path = args.report.expanduser().resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)

    evidence = _collect_seed_evidence(args=args, seed_plan=seed_plan)
    report, failures, status_counts = _build_seed_report(
        dataset_path=dataset_path,
        evidence=evidence,
    )

    return _write_seed_report(
        report_path=report_path,
        report=report,
        failures=failures,
        status_counts=status_counts,
    )


if __name__ == "__main__":
    raise SystemExit(main())
