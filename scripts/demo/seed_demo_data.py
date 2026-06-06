from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, NamedTuple

import httpx

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.demo import seed_demo_http as _seed_demo_http
from scripts.demo.seed_demo_http import fetch_queue_and_mock_state as _fetch_queue_and_mock_state
from scripts.demo.seed_demo_http import poll_history as _poll_history
from scripts.demo.seed_demo_http import request_json as _request_json
from scripts.demo.seed_demo_http import reset_mock_zammad as _reset_mock_zammad
from scripts.demo.seed_demo_http import submit_seed_ingests as _submit_seed_ingests
from scripts.demo.seed_demo_http import wait_for_ready as _wait_for_ready
from scripts.demo.seed_demo_report import build_seed_report as _build_seed_report
from scripts.demo.seed_demo_report import write_seed_report as _write_seed_report

time = _seed_demo_http.time

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
