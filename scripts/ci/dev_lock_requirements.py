#!/usr/bin/env python3
"""Expand the development extra without emitting Chronikwerk as a local dependency."""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    """Print the base, signing, and development requirements from package metadata."""
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)["project"]
    optional = project["optional-dependencies"]
    requirements = [*project["dependencies"], *optional["signing"]]
    requirements.extend(
        dependency for dependency in optional["dev"] if not dependency.startswith("chronikwerk[")
    )
    print("\n".join(requirements))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
