from __future__ import annotations

# Pylint classifies the in-repository test package as standard-library code.
# Keep the third-party pytest import adjacent to its fixture usage instead.
# pylint: disable=wrong-import-order
# ruff: noqa: I001  # Pylint and Ruff classify the in-repository test package differently.

import os
import socket
import sys
from pathlib import Path

import pytest

from test.support.credentials import fake_credential


def pytest_configure() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    src_path = repo_root / "src"
    sys.path.insert(0, str(src_path))

    # Set required env vars for Settings validation during test collection
    os.environ["ZAMMAD__BASE_URL"] = "http://localhost:8080"
    os.environ["ZAMMAD__API_TOKEN"] = fake_credential("api-token")
    os.environ["STORAGE__ROOT"] = str(repo_root / ".test-storage")


@pytest.fixture(autouse=True)
def _disable_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Prevent accidental real network calls in unit/integration tests.

    Respx mocks should still work because they intercept at the HTTP client layer.
    """
    if (os.environ.get("ALLOW_NETWORK_TESTS") or "").strip().lower() in {"1", "true", "yes"}:
        return

    def _blocked(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError(
            "Network access is disabled in tests (set ALLOW_NETWORK_TESTS=1 to override)."
        )

    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket.socket, "connect", _blocked, raising=True)
