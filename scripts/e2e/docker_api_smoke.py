"""Run the disposable Docker stack and prove its public ingest-to-archive path."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Sequence
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from .docker_api_checks import (
        PROCESSED_STATUS,
        E2EFailure,
        assert_artifacts,
        assert_expected_statuses,
        assert_mock_state,
        expected_processed_ticket_ids,
        expected_statuses_from_dataset,
        latest_status_by_ticket,
    )
else:
    _checks = import_module(
        f"{__package__}.docker_api_checks" if __package__ else "docker_api_checks"
    )
    PROCESSED_STATUS = _checks.PROCESSED_STATUS
    E2EFailure = _checks.E2EFailure
    assert_artifacts = _checks.assert_artifacts
    assert_expected_statuses = _checks.assert_expected_statuses
    assert_mock_state = _checks.assert_mock_state
    expected_processed_ticket_ids = _checks.expected_processed_ticket_ids
    expected_statuses_from_dataset = _checks.expected_statuses_from_dataset
    latest_status_by_ticket = _checks.latest_status_by_ticket

__all__ = [
    "E2EFailure",
    "PROCESSED_STATUS",
    "assert_artifacts",
    "assert_expected_statuses",
    "assert_mock_state",
    "expected_processed_ticket_ids",
    "expected_statuses_from_dataset",
    "latest_status_by_ticket",
]

DEFAULT_PROJECT = "chronikwerk-e2e"
DEFAULT_COMPOSE_FILE = Path("infra/e2e/docker-compose.yml")
DEFAULT_DATASET = Path("infra/e2e/dataset.json")
DEFAULT_ARCHIVER_URL = "http://127.0.0.1:18080"
DEFAULT_MOCK_URL = "http://127.0.0.1:18090"
DEFAULT_ADMIN_TOKEN = "e2e-retry-bearer-token-0123456789abcdef"
DEFAULT_HISTORY_TOKEN = "e2e-history-bearer-token-0123456789abcdef"
DEFAULT_HMAC_SECRET = "e2e-webhook-hmac-secret-0123456789abcdef"
DEFAULT_TIMEOUT_SECONDS = 90.0
RETRY_TICKET_ID = 1104


def _parse_args() -> argparse.Namespace:
    """Parse local-only stack coordinates and deterministic test credentials."""
    parser = argparse.ArgumentParser(description="Run Docker API E2E smoke test")
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--compose-file", type=Path, default=DEFAULT_COMPOSE_FILE)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--archiver-url", default=DEFAULT_ARCHIVER_URL)
    parser.add_argument("--mock-url", default=DEFAULT_MOCK_URL)
    parser.add_argument("--admin-token", default=DEFAULT_ADMIN_TOKEN)
    parser.add_argument("--history-token", default=DEFAULT_HISTORY_TOKEN)
    parser.add_argument("--hmac-secret", default=DEFAULT_HMAC_SECRET)
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--keep-stack", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def _load_dataset(path: Path) -> dict[str, Any]:
    """Load the fixture contract before Docker is allowed to mutate local state."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise E2EFailure("dataset phase: dataset must be a JSON object")
    seed_plan = payload.get("seed_plan")
    if not isinstance(seed_plan, list) or not seed_plan:
        raise E2EFailure("dataset phase: dataset.seed_plan must be a non-empty list")
    return payload


def _compose_base(project: str, compose_file: Path) -> list[str]:
    """Build the shared Compose prefix so setup and teardown target one project."""
    return ["docker", "compose", "-p", project, "-f", str(compose_file)]


def _run_command(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """Run only resolved Docker commands, keeping subprocess inputs constrained."""
    command = list(args)
    if not command or command[0] != "docker":
        raise E2EFailure("internal error: only docker commands are supported")
    docker = shutil.which("docker")
    if docker is None:
        raise E2EFailure("startup phase: docker executable not found")
    # Docker path is resolved and args are fixed by this script.
    return subprocess.run(  # nosec B603
        [docker, *command[1:]],
        capture_output=True,
        text=True,
        check=False,
    )


def _run_checked(args: Sequence[str], *, phase: str) -> str:
    """Convert Compose failures into phase-labelled E2E failures with useful output."""
    proc = _run_command(args)
    if proc.returncode != 0:
        stderr = proc.stderr.strip()
        stdout = proc.stdout.strip()
        detail = stderr or stdout or f"exit code {proc.returncode}"
        raise E2EFailure(f"{phase} phase: command failed: {detail}")
    return proc.stdout


def _port_from_url(url: str) -> int:
    """Resolve explicit and scheme-default ports before starting the stack."""
    parsed = httpx.URL(url)
    port = parsed.port
    if port is None:
        return 443 if parsed.scheme == "https" else 80
    return int(port)


def _assert_ports_available(urls: Sequence[str], *, extra_ports: Sequence[int] = ()) -> None:
    """Fail before Compose startup rather than colliding with an unrelated service."""
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
    """Poll a health endpoint until it proves readiness or the bounded deadline expires."""
    deadline = time.monotonic() + timeout_s
    last_error = ""
    while time.monotonic() < deadline:
        try:
            response = client.get(url)
            if response.status_code == 200:
                return
            last_error = f"HTTP {response.status_code}"
        except httpx.HTTPError as exc:
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
    hmac_secret: str | None = None,
) -> tuple[int, Any]:
    """Issue JSON requests and attach the HMAC over the exact serialized bytes."""
    request_headers = dict(headers or {})
    content: bytes | None = None
    if json_body is not None:
        content = json.dumps(json_body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
        if hmac_secret is not None:
            digest = hmac.new(hmac_secret.encode("utf-8"), content, hashlib.sha256).hexdigest()
            request_headers["X-Hub-Signature"] = f"sha256={digest}"
    response = client.request(method, url, headers=request_headers, content=content)
    try:
        return response.status_code, response.json()
    except json.JSONDecodeError:
        return response.status_code, {"raw": response.text}


def _wait_for_statuses(
    client: httpx.Client,
    *,
    archiver_url: str,
    history_token: str,
    expected: dict[int, str],
    timeout_s: float,
) -> dict[str, Any]:
    """Poll volatile history until its terminal states satisfy the fixture oracle."""
    deadline = time.monotonic() + timeout_s
    last_error = ""
    while time.monotonic() < deadline:
        status_code, payload = _request_json(
            client,
            "GET",
            f"{archiver_url}/jobs/history?limit=200",
            headers={"Authorization": f"Bearer {history_token}"},
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
    hmac_secret: str,
) -> None:
    """Reset the double then submit every signed webhook delivery in the seed plan."""
    reset_code, reset_payload = _request_json(client, "POST", f"{mock_url}/__e2e/reset")
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
            hmac_secret=hmac_secret,
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
    """Exercise the protected retry endpoint for the intentionally skipped fixture."""
    status_code, payload = _request_json(
        client,
        "POST",
        f"{archiver_url}/retry/{ticket_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    if status_code != 202 or not isinstance(payload, dict) or payload.get("status") != "accepted":
        raise E2EFailure(f"admin retry phase: HTTP {status_code}: {payload}")


_ARTIFACT_INSPECTOR_SCRIPT = r"""
import hashlib
import json
from pathlib import Path

root = Path("/tmp/archive")
pdfs = sorted(root.rglob("*.pdf"))
sidecars = sorted(root.rglob("*.pdf.json"))
bad_pdfs = [str(path) for path in pdfs if not path.read_bytes().startswith(b"%PDF")]
artifacts = []
for path in sidecars:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        raw_ticket_id = int(payload["ticket_id"])
        pdf_path = Path(str(path)[:-5])
        pdf_sha256 = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
        artifacts.append({
            "ticket_id": raw_ticket_id,
            "pdf": str(pdf_path),
            "sidecar": str(path),
            "sha256": payload.get("sha256"),
            "pdf_sha256": pdf_sha256,
        })
    except (KeyError, ValueError, json.JSONDecodeError, OSError, UnicodeDecodeError):
        continue
print(json.dumps({
    "pdf_count": len(pdfs),
    "sidecar_count": len(sidecars),
    "bad_pdfs": bad_pdfs,
    "artifacts": artifacts,
}, sort_keys=True))
"""


def _inspect_artifacts(project: str, compose_file: Path) -> dict[str, Any]:
    """Inspect files inside the archiver container to avoid host-volume assumptions."""
    stdout = _run_checked(
        [
            *_compose_base(project, compose_file),
            "exec",
            "-T",
            "archiver",
            "python",
            "-c",
            _ARTIFACT_INSPECTOR_SCRIPT,
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
    """Describe the destructive Docker sequence without starting any containers."""
    expected = expected_statuses_from_dataset(dataset)
    print("DRY RUN: Docker API E2E smoke")
    print(f"- Compose project: {args.project}")
    print(f"- Compose file: {args.compose_file}")
    print(f"- Start: docker compose -p {args.project} -f {args.compose_file} up -d --build")
    print(f"- Wait for: GET {args.mock_url}/healthz")
    print(f"- Wait for: GET {args.archiver_url}/healthz")
    print(f"- Reset mock: POST {args.mock_url}/__e2e/reset")
    for ticket_id, status in sorted(expected.items()):
        print(f"- POST /ingest ticket_id={ticket_id} expected={status}")
    print(f"- POST /retry/{RETRY_TICKET_ID} expected={PROCESSED_STATUS}")
    print("- Inspect /tmp/archive in archiver for valid PDFs, sidecars, and checksums")
    print(
        f"- Tear down unless --keep-stack: "
        f"docker compose -p {args.project} down -v --remove-orphans"
    )
    return 0


def _prepare_stack(args: argparse.Namespace, compose_file: Path) -> list[str]:
    """Reject unrelated port owners while recovering a retained project safely."""
    base = _compose_base(args.project, compose_file)
    try:
        _assert_ports_available([args.archiver_url, args.mock_url])
    except E2EFailure:
        running = _run_checked(
            [*base, "ps", "--status", "running", "--quiet"],
            phase="startup",
        )
        if not running.strip():
            raise
        _run_checked([*base, "down", "-v", "--remove-orphans"], phase="cleanup")
        _assert_ports_available([args.archiver_url, args.mock_url])
        return base
    _run_checked([*base, "down", "-v", "--remove-orphans"], phase="cleanup")
    return base


def _wait_for_stack_ready(args: argparse.Namespace, client: httpx.Client) -> None:
    """Require both the mock upstream and archiver health endpoints before ingest."""
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
    """Run initial deliveries, then retry the planned skipped ticket to completion."""
    print("E2E: seeding ingest requests")
    _seed_dataset(
        client,
        archiver_url=args.archiver_url,
        mock_url=args.mock_url,
        dataset=dataset,
        hmac_secret=args.hmac_secret,
    )

    print("E2E: waiting for initial terminal statuses")
    _wait_for_statuses(
        client,
        archiver_url=args.archiver_url,
        history_token=args.history_token,
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
        history_token=args.history_token,
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
    """Validate container-side PDF and sidecar evidence after processing completes."""
    print("E2E: inspecting archived artifacts")
    artifacts = _inspect_artifacts(args.project, compose_file)
    assert_artifacts(artifacts, expected_processed_ticket_ids(expected_after_retry))


def _verify_mock_state(
    client: httpx.Client,
    *,
    mock_url: str,
    expected_processed: set[int],
) -> None:
    """Read the mock's state endpoint and assert Zammad-facing side effects."""
    status_code, payload = _request_json(client, "GET", f"{mock_url}/__e2e/state")
    if status_code != 200 or not isinstance(payload, dict):
        raise E2EFailure(
            f"mock verification phase: invalid state response: {status_code}: {payload}"
        )
    assert_mock_state(payload, expected_processed)


def run(args: argparse.Namespace) -> int:
    """Coordinate the bounded E2E lifecycle and always clean up by default."""
    compose_file = args.compose_file.expanduser().resolve()
    dataset = _load_dataset(args.dataset.expanduser().resolve())
    expected = expected_statuses_from_dataset(dataset)

    if args.dry_run:
        return _print_dry_run(args, dataset)

    base = _prepare_stack(args, compose_file)

    primary_error: BaseException | None = None
    try:
        print("E2E: starting Docker test stack")
        _run_checked([*base, "up", "-d", "--build"], phase="startup")

        with httpx.Client(timeout=20.0) as client:
            _wait_for_stack_ready(args, client)
            expected_after_retry = _exercise_ingest_flow(
                args,
                client,
                dataset=dataset,
                expected=expected,
            )
            _verify_mock_state(
                client,
                mock_url=args.mock_url,
                expected_processed=expected_processed_ticket_ids(expected_after_retry),
            )

        _verify_artifacts(
            args,
            compose_file=compose_file,
            expected_after_retry=expected_after_retry,
        )
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        if args.keep_stack:
            print("E2E: keeping Docker stack running (--keep-stack)")
        else:
            try:
                _run_checked([*base, "down", "-v", "--remove-orphans"], phase="cleanup")
            except E2EFailure as cleanup_error:
                if primary_error is None:
                    raise
                primary_error.add_note(f"cleanup failure: {cleanup_error}")
                print(f"ERROR: cleanup failure: {cleanup_error}", file=sys.stderr)

    print("E2E: PASS")
    return 0


def main() -> int:
    """Translate expected E2E assertion failures into a stable CLI exit status."""
    args = _parse_args()
    try:
        return run(args)
    except E2EFailure as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
