"""Shared file hashing helper for documentation tooling."""

from __future__ import annotations

import hashlib
from pathlib import Path


def ci_1_helper(path: Path) -> str:
    """Hash a file in chunks for the screenshot provenance comparison."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
