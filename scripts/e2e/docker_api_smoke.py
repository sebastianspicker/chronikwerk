from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx

DEFAULT_PROJECT = "zammad-archiver-e2e"
DEFAULT_COMPOSE_FILE = Path("docker-compose.yml")
DEFAULT_DATASET = Path("examples/demo/mock_university_dataset.json")
DEFAULT_ARCHIVER_URL = "http://127.0.0.1:18080"
DEFAULT_MOCK_URL = "http://127.0.0.1:18090"
DEFAULT_ADMIN_TOKEN = "demo-admin-token"
DEFAULT_TIMEOUT_SECONDS = 90.0
RETRY_TICKET_ID = 1104
PROCESSED_STATUS = "processed"


class E2EFailure(RuntimeError):
    """Raised when the Docker API E2E lane cannot prove the expected behavior."""


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Docker API E2E smoke test")
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--compose-file", type=Path, default=DEFAULT_COMPOSE_FILE)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--archiver-url", default=DEFAULT_ARCHIVER_URL)
    parser.add_argument("--mock-url", default=DEFAULT_MOCK_URL)
    parser.add_argument("--admin-token", default=DEFAULT_ADMIN_TOKEN)
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--keep-stack", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def _load_dataset(path: Path) -> dict[str, Any]:
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
    # /jobs/history returns newest first; keep the first status per ticket.
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
            mismatches.append(
                f"ticket {ticket_id}: expected {expected_status!r}, got {actual!r}"
            )
    if mismatches:
        raise E2EFailure("history phase: terminal status mismatch: " + "; ".join(mismatches))


def assert_artifacts(
    artifact_payload: dict[str, Any],
    expected_ticket_ids: set[int],
) -> None:
    pdf_count = int(artifact_payload.get("pdf_count", 0))
    bad_pdfs = [str(path) for path in artifact_payload.get("bad_pdfs", [])]
    sidecar_ticket_ids = {int(value) for value in artifact_payload.get("sidecar_ticket_ids", [])}
    missing_sidecars = sorted(expected_ticket_ids - sidecar_ticket_ids)

    if pdf_count < len(expected_ticket_ids):
        raise E2EFailure(
            f"artifact phase: expected at least {len(expected_ticket_ids)} PDFs, got {pdf_count}"
        )
    if bad_pdfs:
        raise E2EFailure("artifact phase: PDFs without %PDF header: " + ", ".join(bad_pdfs))
    if missing_sidecars:
        raise E2EFailure(
            "artifact phase: missing sidecars for ticket IDs "
            + ", ".join(str(ticket_id) for ticket_id in missing_sidecars)
        )


def _compose_base(project: str, compose_file: Path) -> list[str]:
    return ["docker", "compose", "-p", project, "-f", str(compose_file)]


def _run_command(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    command = list(args)
    if not command or command[0] != "docker":
        raise E2EFailure("internal error: only docker commands are supported")
    return subprocess.run(
        ["docker", *command[1:]],
        capture_output=True,
        text=True,
        check=False,
    )


def _run_checked(args: Sequence[str], *, phase: str) -> str:
    proc = _run_command(args)
    if proc.returncode != 0:
        stderr = proc.stderr.strip()
        stdout = proc.stdout.strip()
        detail = stderr or stdout or f"exit code {proc.returncode}"
        raise E2EFailure(f"{phase} phase: command failed: {detail}")
    return proc.stdout


def _port_from_url(url: str) -> int:
    parsed = httpx.URL(url)
    port = parsed.port
    if port is None:
        return 443 if parsed.scheme == "https" else 80
    return int(port)


def _assert_ports_available(urls: Sequence[str], *, extra_ports: Sequence[int] = ()) -> None:
    blocked: list[str] = []
    ports = [_port_from_url(url) for url in urls]
    ports.extend(int(port) for port in extra_ports)
    for port in ports:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.25)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                blocked.append(f"127.0.0.1:{port}")
    if blocked:
        raise E2EFailure(
            "startup phase: required port(s) already in use: " + ", ".join(sorted(blocked))
        )


def _wait_http_ok(client: httpx.Client, label: str, url: str, *, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    last_error = ""
    while time.monotonic() < deadline:
        try:
            response = client.get(url)
            if response.status_code == 200:
                return
            last_error = f"HTTP {response.status_code}"
        except Exception as exc:
            last_error = str(exc)
        time.sleep(1.0)
    raise E2EFailure(f"readiness phase: {label} not ready at {url}: {last_error}")


def _request_json(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: Any | None = None,
) -> tuple[int, Any]:
    response = client.request(method, url, headers=headers, json=json_body)
    try:
        return response.status_code, response.json()
    except Exception:
        return response.status_code, {"raw": response.text}


def _wait_for_statuses(
    client: httpx.Client,
    *,
    archiver_url: str,
    admin_token: str,
    expected: dict[int, str],
    timeout_s: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    last_error = ""
    while time.monotonic() < deadline:
        status_code, payload = _request_json(
            client,
            "GET",
            f"{archiver_url}/jobs/history?limit=200",
        )
        if status_code == 200 and isinstance(payload, dict):
            try:
                assert_expected_statuses(payload, expected)
                return payload
            except E2EFailure as exc:
                last_error = str(exc)
        else:
            last_error = f"HTTP {status_code}: {payload}"
        time.sleep(1.0)
    raise E2EFailure(f"history phase: timed out waiting for expected statuses: {last_error}")


def _seed_dataset(
    client: httpx.Client,
    *,
    archiver_url: str,
    mock_url: str,
    dataset: dict[str, Any],
) -> None:
    reset_code, reset_payload = _request_json(client, "POST", f"{mock_url}/__demo/reset")
    if reset_code != 200:
        raise E2EFailure(f"ingest phase: mock reset failed ({reset_code}): {reset_payload}")

    seed_plan = dataset["seed_plan"]
    for item in seed_plan:
        ticket_id = int(item["ticket_id"])
        delivery_id = str(item.get("delivery_id") or f"e2e-delivery-{ticket_id}")
        user_login = str(item.get("user_login") or "e2e.agent")
        status_code, payload = _request_json(
            client,
            "POST",
            f"{archiver_url}/ingest",
            headers={"X-Zammad-Delivery": delivery_id},
            json_body={"ticket": {"id": ticket_id}, "user": {"login": user_login}},
        )
        if status_code != 202:
            raise E2EFailure(
                f"ingest phase: ticket {ticket_id} returned HTTP {status_code}: {payload}"
            )


def _retry_ticket(
    client: httpx.Client,
    *,
    archiver_url: str,
    admin_token: str,
    ticket_id: int,
) -> None:
    status_code, payload = _request_json(
        client,
        "POST",
        f"{archiver_url}/retry/{ticket_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    if status_code != 200 or not isinstance(payload, dict) or payload.get("status") != "accepted":
        raise E2EFailure(f"admin retry phase: HTTP {status_code}: {payload}")


def expected_processed_ticket_ids(expected_statuses: dict[int, str]) -> set[int]:
    return {
        ticket_id
        for ticket_id, status in expected_statuses.items()
        if status == PROCESSED_STATUS
    }


def _inspect_artifacts(project: str, compose_file: Path) -> dict[str, Any]:
    inspector = r"""
import json
from pathlib import Path

root = Path("/tmp/archive")
pdfs = sorted(root.rglob("*.pdf"))
sidecars = sorted(root.rglob("*.pdf.json"))
bad_pdfs = [str(path) for path in pdfs if not path.read_bytes().startswith(b"%PDF")]
ticket_ids = []
for path in sidecars:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        continue
    raw_ticket_id = payload.get("ticket_id")
    if raw_ticket_id is not None:
        ticket_ids.append(int(raw_ticket_id))
print(json.dumps({
    "pdf_count": len(pdfs),
    "sidecar_count": len(sidecars),
    "bad_pdfs": bad_pdfs,
    "sidecar_ticket_ids": sorted(set(ticket_ids)),
}, sort_keys=True))
"""
    stdout = _run_checked(
        [
            *_compose_base(project, compose_file),
            "exec",
            "-T",
            "archiver-demo",
            "python",
            "-c",
            inspector,
        ],
        phase="artifact",
    )
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise E2EFailure(f"artifact phase: invalid JSON from container: {stdout!r}") from exc
    if not isinstance(payload, dict):
        raise E2EFailure("artifact phase: container returned non-object JSON")
    return payload


def _print_dry_run(args: argparse.Namespace, dataset: dict[str, Any]) -> int:
    expected = expected_statuses_from_dataset(dataset)
    print("DRY RUN: Docker API E2E smoke")
    print(f"- Compose project: {args.project}")
    print(f"- Compose file: {args.compose_file}")
    print(f"- Start: docker compose -p {args.project} -f {args.compose_file} up -d --build")
    print(f"- Wait for: GET {args.mock_url}/healthz")
    print(f"- Wait for: GET {args.archiver_url}/healthz")
    print(f"- Reset mock: POST {args.mock_url}/__demo/reset")
    for ticket_id, status in sorted(expected.items()):
        print(f"- POST /ingest ticket_id={ticket_id} expected={status}")
    print(f"- POST /retry/{RETRY_TICKET_ID} expected={PROCESSED_STATUS}")
    print("- Inspect /tmp/archive in archiver-demo for PDFs and sidecars")
    print(
        f"- Tear down unless --keep-stack: "
        f"docker compose -p {args.project} down -v --remove-orphans"
    )
    return 0


def _prepare_stack(args: argparse.Namespace, compose_file: Path) -> list[str]:
    base = _compose_base(args.project, compose_file)
    _run_checked([*base, "down", "-v", "--remove-orphans"], phase="cleanup")
    _assert_ports_available(
        [args.archiver_url, args.mock_url],
    )
    return base


def _wait_for_stack_ready(args: argparse.Namespace, client: httpx.Client) -> None:
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


def _exercise_ingest_flow(
    args: argparse.Namespace,
    client: httpx.Client,
    *,
    dataset: dict[str, Any],
    expected: dict[int, str],
) -> dict[int, str]:
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


def _verify_artifacts(
    args: argparse.Namespace,
    *,
    compose_file: Path,
    expected_after_retry: dict[int, str],
) -> None:
    print("E2E: inspecting archived artifacts")
    artifacts = _inspect_artifacts(args.project, compose_file)
    assert_artifacts(artifacts, expected_processed_ticket_ids(expected_after_retry))


def run(args: argparse.Namespace) -> int:
    compose_file = args.compose_file.expanduser().resolve()
    dataset = _load_dataset(args.dataset.expanduser().resolve())
    expected = expected_statuses_from_dataset(dataset)

    if args.dry_run:
        return _print_dry_run(args, dataset)

    base = _prepare_stack(args, compose_file)

    try:
        print("E2E: starting Docker demo stack")
        _run_checked([*base, "up", "-d", "--build"], phase="startup")

        with httpx.Client(timeout=20.0) as client:
            _wait_for_stack_ready(args, client)
            expected_after_retry = _exercise_ingest_flow(
                args,
                client,
                dataset=dataset,
                expected=expected,
            )

        _verify_artifacts(
            args,
            compose_file=compose_file,
            expected_after_retry=expected_after_retry,
        )
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
