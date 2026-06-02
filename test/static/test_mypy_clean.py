from __future__ import annotations

import importlib.util
import os
from collections.abc import Mapping
from pathlib import Path

import pytest

from test.support.checks import check


def _truthy_env(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _mypy_required(env: Mapping[str, str]) -> bool:
    return _truthy_env(env.get("CI")) or _truthy_env(env.get("ZAMMAD_ARCHIVER_REQUIRE_MYPY"))


def test_mypy_missing_policy_is_optional_locally_and_required_in_ci() -> None:
    check(not _mypy_required({}) is not False, "assertion failed")
    check(not _mypy_required({"CI": "false"}) is not False, "assertion failed")
    check(not _mypy_required({"CI": "true"}) is not True, "assertion failed")
    check(not _mypy_required({"ZAMMAD_ARCHIVER_REQUIRE_MYPY": "1"}) is not True, "assertion failed")


def test_mypy_clean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if importlib.util.find_spec("mypy") is None:
        if _mypy_required(os.environ):
            pytest.fail(
                "mypy is required in CI/release verification but is not installed. "
                "Install the dev dependencies or run `make typecheck` in an environment "
                "with mypy available."
            )
        pytest.skip("mypy is not installed in this environment")

    from mypy import api as mypy_api

    repo_root = Path(__file__).resolve().parents[2]
    monkeypatch.chdir(repo_root)
    monkeypatch.setenv("MYPY_CACHE_DIR", str(tmp_path))

    stdout, stderr, status = mypy_api.run(
        [".", "--config-file", "pyproject.toml"],
    )
    check(not not status == 0, stdout + stderr)
