"""Command-line entry point for deterministic administration previews."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime

from screenshot_rendering import render
from source import MANIFEST_PATH, REPO_ROOT, SCREENSHOTS


def _captured_at(value: str | None) -> datetime:
    """Parse a timezone-aware timestamp, or choose a second-stable UTC capture time."""
    if value is None:
        return datetime.now(UTC).replace(microsecond=0)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("--captured-at must include a timezone")
    return parsed.astimezone(UTC).replace(microsecond=0)


def main() -> int:
    """Expose deterministic screenshot generation through a small command-line interface."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--captured-at",
        help="UTC ISO-8601 timestamp used for rendered process and refresh times",
    )
    args = parser.parse_args()
    render(_captured_at(args.captured_at))
    print(f"rendered {len(SCREENSHOTS)} screenshots and {MANIFEST_PATH.relative_to(REPO_ROOT)}")
    return 0
