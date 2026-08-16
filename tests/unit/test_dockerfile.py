"""Characterize the production image's runtime contract."""

from __future__ import annotations

from pathlib import Path


def _runtime_stage() -> str:
    """Return the production runtime stage from the repository Dockerfile."""
    dockerfile = Path(__file__).resolve().parents[2] / "Dockerfile"
    _, runtime = dockerfile.read_text("utf-8").split(" AS runtime\n", maxsplit=1)
    return runtime


def test_production_runtime_uses_the_unprivileged_service_account() -> None:
    runtime = _runtime_stage()
    user_lines = [line for line in runtime.splitlines() if line.startswith("USER ")]

    assert user_lines == ["USER app:app"]


def test_production_runtime_healthcheck_uses_the_configured_port() -> None:
    runtime = _runtime_stage()
    healthcheck_start = runtime.index("HEALTHCHECK ")
    command_start = runtime.index('CMD ["chronikwerk"]')
    healthcheck = runtime[healthcheck_start:command_start]

    assert "SERVER__PORT" in healthcheck
    assert "/healthz" in healthcheck
