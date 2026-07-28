"""Verify the process-local queue, deduplication, and locking model."""

from __future__ import annotations

import tomllib
from pathlib import Path


def test_process_model_no_broker_dependencies() -> None:
    """NFR10: No broker dependencies."""
    repo_root = Path(__file__).resolve().parents[2]
    pyproject = repo_root / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text())
    deps = data.get("project", {}).get("dependencies", [])
    forbidden = ("celery", "rabbitmq", "pika", "kombu")
    for dep in deps:
        dep_lower = dep.lower()
        for word in forbidden:
            assert word not in dep_lower, (
                f"NFR10: required dependency {dep!r} must not contain {word!r}"
            )
