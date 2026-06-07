from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

import httpx

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.e2e import docker_api_smoke_compose as _compose_module
from scripts.e2e.docker_api_smoke_compose import (
    CommandResult as _CommandResult,
)
from scripts.e2e.docker_api_smoke_compose import (
    assert_ports_available as _assert_ports_available,
)
from scripts.e2e.docker_api_smoke_compose import (
    compose_base as _compose_base,
)
from scripts.e2e.docker_api_smoke_compose import (
    inspect_artifacts as _inspect_artifacts,
)
from scripts.e2e.docker_api_smoke_compose import (
    port_from_url as _port_from_url,
)
from scripts.e2e.docker_api_smoke_compose import (
    run_checked as _run_checked,
)
from scripts.e2e.docker_api_smoke_compose import (
    run_command as _run_command,
)
from scripts.e2e.docker_api_smoke_compose import (
    run_compose_exec as _run_compose_exec,
)
from scripts.e2e.docker_api_smoke_contracts import (
    PROCESSED_STATUS,
    E2EFailure,
    assert_artifacts,
    assert_expected_statuses,
    expected_processed_ticket_ids,
    expected_statuses_from_dataset,
    latest_status_by_ticket,
)
from scripts.e2e.docker_api_smoke_contracts import (
    load_dataset as _load_dataset,
)
from scripts.e2e.docker_api_smoke_http import (
    request_json as _request_json,
)
from scripts.e2e.docker_api_smoke_http import (
    retry_ticket as _retry_ticket,
)
from scripts.e2e.docker_api_smoke_http import (
    seed_dataset as _seed_dataset,
)
from scripts.e2e.docker_api_smoke_http import (
    wait_for_statuses as _wait_for_statuses,
)
from scripts.e2e.docker_api_smoke_http import (
    wait_http_ok as _wait_http_ok,
)

shutil = _compose_module.shutil

__all__ = [
    "E2EFailure",
    "PROCESSED_STATUS",
    "_CommandResult",
    "_assert_ports_available",
    "_compose_base",
    "_inspect_artifacts",
    "_port_from_url",
    "_request_json",
    "_retry_ticket",
    "_run_checked",
    "_run_command",
    "_run_compose_exec",
    "_seed_dataset",
    "_wait_for_statuses",
    "_wait_http_ok",
    "assert_artifacts",
    "assert_expected_statuses",
    "expected_processed_ticket_ids",
    "expected_statuses_from_dataset",
    "latest_status_by_ticket",
    "main",
    "run",
    "shutil",
]

DEFAULT_PROJECT = "zammad-archiver-e2e"
DEFAULT_COMPOSE_FILE = Path("docker-compose.demo.yml")
DEFAULT_DATASET = Path("examples/demo/mock_university_dataset.json")
DEFAULT_ARCHIVER_URL = "http://127.0.0.1:18080"
DEFAULT_MOCK_URL = "http://127.0.0.1:18090"
ADMIN_AUTH_ENV = "ZAMMAD_ARCHIVER_DEMO_ADMIN_TOKEN"
DEFAULT_REDIS_PORT = 16379
DEFAULT_TIMEOUT_SECONDS = 90.0
RETRY_TICKET_ID = 1104


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Docker API E2E smoke test")
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--compose-file", type=Path, default=DEFAULT_COMPOSE_FILE)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--archiver-url", default=DEFAULT_ARCHIVER_URL)
    parser.add_argument("--mock-url", default=DEFAULT_MOCK_URL)
    parser.add_argument("--admin-token", default=os.environ.get(ADMIN_AUTH_ENV))
    parser.add_argument("--redis-port", type=int, default=DEFAULT_REDIS_PORT)
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--keep-stack", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def _print_dry_run(args: argparse.Namespace, dataset: dict[str, Any]) -> int:
    expected = expected_statuses_from_dataset(dataset)
    print("DRY RUN: Docker API E2E smoke")
    print(f"- Compose project: {args.project}")
    print(f"- Compose file: {args.compose_file}")
    print(f"- Start: docker compose -p {args.project} -f {args.compose_file} up -d --build")
    print(f"- Wait for: GET {args.mock_url}/healthz")
    print(f"- Wait for: GET {args.archiver_url}/healthz")
    print(f"- Required Redis port: 127.0.0.1:{args.redis_port}")
    print(f"- Reset mock: POST {args.mock_url}/__demo/reset")
    for ticket_id, status in sorted(expected.items()):
        print(f"- POST /ingest ticket_id={ticket_id} expected={status}")
    print(f"- POST /admin/api/retry/{RETRY_TICKET_ID} expected={PROCESSED_STATUS}")
    print("- Inspect /tmp/archive in archiver-demo for PDFs and sidecars")
    print(
        f"- Tear down unless --keep-stack: "
        f"docker compose -p {args.project} down -v --remove-orphans"
    )
    return 0


def _start_stack(args: argparse.Namespace, compose_file: Path) -> list[str]:
    base = _compose_base(args.project, compose_file)
    _run_checked([*base, "down", "-v", "--remove-orphans"], phase="cleanup")
    _assert_ports_available(
        [args.archiver_url, args.mock_url],
        extra_ports=[args.redis_port],
    )
    print("E2E: starting Docker demo stack")
    _run_checked([*base, "up", "-d", "--build"], phase="startup")
    return base


def _run_initial_ingest_phase(
    client: httpx.Client,
    *,
    args: argparse.Namespace,
    dataset: dict[str, Any],
    expected: dict[int, str],
) -> None:
    _wait_http_ok(
        client,
        "mock-zammad",
        f"{args.mock_url}/healthz",
        timeout_s=args.timeout_seconds,
    )
    _wait_http_ok(
        client,
        "archiver",
        f"{args.archiver_url}/healthz",
        timeout_s=args.timeout_seconds,
    )

    print("E2E: seeding ingest requests")
    _seed_dataset(
        client,
        archiver_url=args.archiver_url,
        mock_url=args.mock_url,
        dataset=dataset,
    )

    print("E2E: waiting for initial terminal statuses")
    _wait_for_statuses(
        client,
        archiver_url=args.archiver_url,
        admin_token=args.admin_token,
        expected=expected,
        timeout_s=args.timeout_seconds,
    )


def _run_retry_phase(
    client: httpx.Client, *, args: argparse.Namespace, expected: dict[int, str]
) -> dict[int, str]:
    print(f"E2E: retrying skipped ticket {RETRY_TICKET_ID}")
    _retry_ticket(
        client,
        archiver_url=args.archiver_url,
        admin_token=args.admin_token,
        ticket_id=RETRY_TICKET_ID,
    )
    expected_after_retry = dict(expected)
    expected_after_retry[RETRY_TICKET_ID] = PROCESSED_STATUS
    _wait_for_statuses(
        client,
        archiver_url=args.archiver_url,
        admin_token=args.admin_token,
        expected=expected_after_retry,
        timeout_s=args.timeout_seconds,
    )
    return expected_after_retry


def run(args: argparse.Namespace) -> int:
    compose_file = args.compose_file.expanduser().resolve()
    dataset = _load_dataset(args.dataset.expanduser().resolve())
    expected = expected_statuses_from_dataset(dataset)

    if args.dry_run:
        return _print_dry_run(args, dataset)
    if not args.admin_token:
        raise E2EFailure(f"startup phase: set --admin-token or {ADMIN_AUTH_ENV}")

    base = _start_stack(args, compose_file)

    try:
        with httpx.Client(timeout=20.0) as client:
            _run_initial_ingest_phase(
                client, args=args, dataset=dataset, expected=expected
            )
            expected_after_retry = _run_retry_phase(client, args=args, expected=expected)

        print("E2E: inspecting archived artifacts")
        artifacts = _inspect_artifacts(args.project, compose_file)
        assert_artifacts(artifacts, expected_processed_ticket_ids(expected_after_retry))
        print("E2E: PASS")
        return 0
    finally:
        if args.keep_stack:
            print("E2E: keeping Docker stack running (--keep-stack)")
        else:
            _run_command([*base, "down", "-v", "--remove-orphans"])


def main() -> int:
    args = _parse_args()
    try:
        return run(args)
    except E2EFailure as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
