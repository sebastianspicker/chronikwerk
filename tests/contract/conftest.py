"""Shared fixtures for contract tests that load configuration from files."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

_AMBIENT_NAMES = (
    "CONFIG_PATH",
    "ZAMMAD__BASE_URL",
    "ZAMMAD__API_TOKEN",
    "STORAGE__ROOT",
    "ZAMMAD_ORIGIN",
    "ZAMMAD_API_TOKEN",
    "ZAMMAD_TIMEOUT_SECONDS",
    "ZAMMAD_ALLOW_PRIVATE_ORIGIN",
    "ZAMMAD_TRUST_ENV",
)


@pytest.fixture
def isolated_config_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Remove ambient configuration variables and run from an empty directory."""
    for name in _AMBIENT_NAMES:
        monkeypatch.delenv(name, raising=False)
    for name in [key for key in os.environ if key.startswith("WORKFLOW__")]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path
