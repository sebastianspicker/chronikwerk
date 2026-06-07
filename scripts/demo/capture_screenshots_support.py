from __future__ import annotations

import argparse
import asyncio
import shutil
import time
from pathlib import Path
from typing import Any, NamedTuple

import httpx

SHOT_FILENAMES = [
    "01-admin-token-screen.png",
    "02-admin-queue-stats.png",
    "03-admin-history-all.png",
    "04-admin-history-filtered-ticket.png",
    "05-admin-retry-action.png",
    "06-admin-dlq-before-drain.png",
    "07-admin-dlq-after-drain.png",
    "08-api-401-unauthorized.png",
    "09-api-503-backend-unavailable.png",
    "10-admin-mobile-viewport.png",
]
ADMIN_AUTH_ENV = "ZAMMAD_ARCHIVER_DEMO_ADMIN_TOKEN"


class CommandResult(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


async def run_compose_exec(
    compose_file: Path, args: tuple[str, ...], *, executable: str
) -> CommandResult:
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
    return CommandResult(
        returncode=int(proc.returncode or 0),
        stdout=stdout.decode(),
        stderr=stderr.decode(),
    )


def compose(compose_file: Path, *args: str) -> CommandResult:
    docker = shutil.which("docker")
    if docker is None:
        raise RuntimeError("docker executable not found on PATH")
    compose_path = compose_file.expanduser()
    if not compose_path.is_file():
        raise RuntimeError(f"compose file not found: {compose_file}")
    return asyncio.run(run_compose_exec(compose_path.resolve(), args, executable=docker))


def wait_http_ok(label: str, url: str, *, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    last_error = ""
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=3.0)
            if response.status_code == 200:
                return
            last_error = f"HTTP {response.status_code}"
        except Exception as exc:  # pragma: no cover - defensive
            last_error = str(exc)
        time.sleep(1.0)
    raise RuntimeError(f"{label} not ready: {url} ({last_error})")


def dry_run(args: argparse.Namespace) -> int:
    print("DRY RUN: screenshot capture plan")
    print(f"- Base URL: {args.base_url}")
    print(f"- Output directory: {args.output_dir}")
    print("- Expected files:")
    for name in SHOT_FILENAMES:
        print(f"  - {name}")
    print(f"- docker compose -f {args.compose_file} stop redis-demo")
    print(f"- docker compose -f {args.compose_file} start redis-demo")
    return 0


def import_playwright() -> Any:
    try:
        from playwright.sync_api import (
            Error,
            sync_playwright,
        )
        from playwright.sync_api import (
            TimeoutError as PlaywrightTimeoutError,
        )
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed. Install dev dependencies first, "
            "e.g. pip install -e '.[dev]'."
        ) from exc
    return sync_playwright, Error, PlaywrightTimeoutError


def check_browser_installation(*, headed: bool) -> None:
    sync_playwright, Error, _ = import_playwright()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not headed)
            browser.close()
    except Error as exc:
        raise RuntimeError(
            "Playwright browser is not installed. Run: python -m playwright install chromium"
        ) from exc
