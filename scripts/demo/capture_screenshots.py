from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
from pathlib import Path

from scripts.demo.capture_screenshots_flow import capture as _capture
from scripts.demo.capture_screenshots_support import ADMIN_AUTH_ENV, SHOT_FILENAMES
from scripts.demo.capture_screenshots_support import CommandResult as _CommandResult
from scripts.demo.capture_screenshots_support import (
    check_browser_installation as _check_browser_installation,
)
from scripts.demo.capture_screenshots_support import dry_run as _dry_run
from scripts.demo.capture_screenshots_support import run_compose_exec as _run_compose_exec

__all__ = [
    "SHOT_FILENAMES",
    "_CommandResult",
    "_compose",
    "_dry_run",
    "_run_compose_exec",
    "shutil",
]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture local demo screenshots via Playwright")
    parser.add_argument("--base-url", default="http://127.0.0.1:18080")
    parser.add_argument("--output-dir", type=Path, default=Path("docs/assets/demo"))
    parser.add_argument("--token", default=os.environ.get(ADMIN_AUTH_ENV))
    parser.add_argument("--filter-ticket-id", type=int, default=1101)
    parser.add_argument("--retry-ticket-id", type=int, default=1104)
    parser.add_argument("--compose-file", type=Path, default=Path("docker-compose.demo.yml"))
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def _compose(compose_file: Path, *args: str) -> _CommandResult:
    docker = shutil.which("docker")
    if docker is None:
        raise RuntimeError("docker executable not found on PATH")
    compose_path = compose_file.expanduser()
    if not compose_path.is_file():
        raise RuntimeError(f"compose file not found: {compose_file}")
    return asyncio.run(_run_compose_exec(compose_path.resolve(), args, executable=docker))


def main() -> int:
    args = _parse_args()

    if args.dry_run:
        return _dry_run(args)

    if args.check_only:
        _check_browser_installation(headed=args.headed)
        print("Playwright Chromium check OK")
        return 0
    if not args.token:
        print(f"ERROR: set --token or {ADMIN_AUTH_ENV}", file=sys.stderr)
        return 2

    try:
        return _capture(args)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
