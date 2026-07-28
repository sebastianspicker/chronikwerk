"""Verify the supported Python runtime and package version contract."""

from __future__ import annotations

import tomllib
from pathlib import Path


def test_python_runtime_pyproject_requires_python_314_plus() -> None:
    """NFR9: pyproject.toml must require Python >=3.14."""
    repo_root = Path(__file__).resolve().parents[2]
    pyproject = repo_root / "pyproject.toml"
    assert pyproject.is_file()
    with pyproject.open("rb") as handle:
        value = tomllib.load(handle)["project"]["requires-python"]
    assert value == ">=3.14"


def test_python_runtime_runtime_version_matches_package_metadata() -> None:
    """NFR9: Runtime status and package metadata must report the same version."""
    from chronikwerk._version import __version__

    repo_root = Path(__file__).resolve().parents[2]
    with (repo_root / "pyproject.toml").open("rb") as handle:
        project_version = tomllib.load(handle)["project"]["version"]

    assert __version__ == project_version
