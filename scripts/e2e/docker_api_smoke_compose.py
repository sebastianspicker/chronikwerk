from __future__ import annotations

import asyncio
import json
import shutil
import socket
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NamedTuple

import httpx

from scripts.e2e.docker_api_smoke_contracts import E2EFailure


class CommandResult(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


def compose_base(project: str, compose_file: Path) -> list[str]:
    docker = shutil.which("docker")
    if docker is None:
        raise E2EFailure("startup phase: docker executable not found on PATH")
    compose_path = compose_file.expanduser()
    if not compose_path.is_file():
        raise E2EFailure(f"startup phase: compose file not found: {compose_file}")
    return ["docker", "compose", "-p", project, "-f", str(compose_path.resolve())]


async def run_compose_exec(
    project: str,
    compose_file: str,
    args: Sequence[str],
    *,
    executable: str,
) -> CommandResult:
    proc = await asyncio.create_subprocess_exec(
        "docker",
        "compose",
        "-p",
        project,
        "-f",
        compose_file,
        *list(args),
        executable=executable,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return CommandResult(
        returncode=int(proc.returncode or 0),
        stdout=stdout.decode(),
        stderr=stderr.decode(),
    )


def run_command(args: Sequence[str]) -> CommandResult:
    docker = shutil.which("docker")
    if docker is None:
        raise E2EFailure("startup phase: docker executable not found on PATH")
    command = list(args)
    if len(command) < 6 or command[:2] != ["docker", "compose"]:
        raise E2EFailure("startup phase: expected docker compose command")
    if command[2] != "-p" or command[4] != "-f":
        raise E2EFailure("startup phase: expected docker compose project and file flags")
    return asyncio.run(run_compose_exec(command[3], command[5], command[6:], executable=docker))


def run_checked(args: Sequence[str], *, phase: str) -> str:
    proc = run_command(args)
    if proc.returncode != 0:
        stderr = proc.stderr.strip()
        stdout = proc.stdout.strip()
        detail = stderr or stdout or f"exit code {proc.returncode}"
        raise E2EFailure(f"{phase} phase: command failed: {detail}")
    return proc.stdout


def port_from_url(url: str) -> int:
    parsed = httpx.URL(url)
    port = parsed.port
    if port is None:
        return 443 if parsed.scheme == "https" else 80
    return int(port)


def assert_ports_available(urls: Sequence[str], *, extra_ports: Sequence[int] = ()) -> None:
    blocked: list[str] = []
    ports = [port_from_url(url) for url in urls]
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


def inspect_artifacts(project: str, compose_file: Path) -> dict[str, Any]:
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
    stdout = run_checked(
        [
            *compose_base(project, compose_file),
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
