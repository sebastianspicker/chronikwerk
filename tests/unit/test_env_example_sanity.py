"""Verifies the environment example uses canonical, non-forcing defaults."""

from __future__ import annotations

from pathlib import Path

from tests.support.env_file_helpers import parse_env_file


def _parse_env_example(repo_root: Path) -> dict[str, str]:
    """Parse `.env.example`, skipping when the host locks the file."""
    try:
        return parse_env_file(repo_root / ".env.example")
    except PermissionError:
        import pytest

        pytest.skip("PermissionError reading .env.example (system locked)")


def test_env_example_does_not_force_missing_config_path() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    env = _parse_env_example(repo_root)

    # `CONFIG_PATH` is optional; setting it to a missing file causes startup to fail.
    assert env.get("CONFIG_PATH", "") == ""


def test_env_example_uses_canonical_zammad_base_url_var() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    env = _parse_env_example(repo_root)

    assert "ZAMMAD__BASE_URL" in env
