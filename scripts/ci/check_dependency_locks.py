#!/usr/bin/env python3
"""Validate committed dependency locks before installation or freshness checks."""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LOCK_DIR = REPO_ROOT / "requirements"
LOCK_NAMES = ("base.lock", "signing.lock", "dev.lock", "tools.lock")
PIN_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+==[^\\\s]+(?: ; .+)? \\$")


def _lock_errors(path: Path) -> list[str]:
    if not path.is_file():
        return [f"missing lock: {path.relative_to(REPO_ROOT)}"]
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    pins = [index for index, line in enumerate(lines) if PIN_PATTERN.match(line)]
    errors = [] if pins else [f"{path.name}: no exact dependency pins"]
    if " @ file:" in text or "file://" in text:
        errors.append(f"{path.name}: local filesystem dependency is not reproducible")
    block_ends = pins[1:] + [len(lines)]
    for index, block_end in zip(pins, block_ends, strict=True):
        if not any("--hash=sha256:" in line for line in lines[index + 1 : block_end]):
            errors.append(f"{path.name}:{index + 1}: pin has no SHA-256 hash")
    return errors


def main() -> int:
    """Report malformed or missing lock artifacts."""
    errors = [error for name in LOCK_NAMES for error in _lock_errors(LOCK_DIR / name)]
    for error in errors:
        print(f"dependency-locks-check: {error}", file=sys.stderr)
    if errors:
        return 1
    print(f"dependency-locks-check: OK ({len(LOCK_NAMES)} hashed locks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
