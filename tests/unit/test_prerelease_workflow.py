"""Verifies prerelease workflow gates, artifacts, and release-note validation."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path


def _workflow(name: str) -> str:
    """Load one workflow YAML file for structural assertions."""
    return (Path(__file__).resolve().parents[2] / ".github/workflows" / name).read_text(
        encoding="utf-8"
    )


def _prerelease_validation_script() -> str:
    """Extract the release-validation script embedded in the workflow."""
    workflow = _workflow("prerelease.yml")
    marker = "          python - <<'PY'\n"
    _, remainder = workflow.split(marker, maxsplit=1)
    script, _ = remainder.split("\n          PY", maxsplit=1)
    return textwrap.dedent(script)


def test_ci_and_security_are_reusable_without_dropping_existing_triggers() -> None:
    ci = _workflow("ci.yml")
    security = _workflow("security.yml")
    assert "workflow_call:" in ci and "push:" in ci and "pull_request:" in ci
    assert "workflow_call:" in security and "push:" in security and "schedule:" in security


def test_docker_workflow_builds_without_registry_publication() -> None:
    docker = _workflow("docker.yml")
    assert "packages: write" not in docker
    assert "docker/login-action@" not in docker
    assert "push: false" in docker


def test_prerelease_is_tag_only_and_gated_by_both_workflows() -> None:
    release = _workflow("prerelease.yml")
    assert '"v*-alpha.*"' in release
    assert '"v*-beta.*"' in release
    assert '"v*-rc.*"' in release
    assert "workflow_dispatch" not in release
    assert "uses: ./.github/workflows/ci.yml" in release
    assert "uses: ./.github/workflows/security.yml" in release
    assert "needs: [ci, security]" in release


def test_prerelease_downloads_verified_dist_without_rebuilding() -> None:
    release = _workflow("prerelease.yml")
    assert "actions/download-artifact@" in release
    assert "name: dist" in release
    assert "sha256sum *.whl *.tar.gz" in release
    assert "python -m build" not in release


def test_prerelease_fails_closed_on_version_or_changelog_mismatch() -> None:
    release = _workflow("prerelease.yml")
    assert "tomllib" in release
    assert '"alpha": "a"' in release
    assert '"beta": "b"' in release
    assert "tag/version mismatch" in release
    assert "missing exact CHANGELOG section" in release
    assert "empty CHANGELOG section" in release


def test_prerelease_accepts_the_prepared_alpha_changelog_section(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    shutil.copy2(repo_root / "pyproject.toml", tmp_path / "pyproject.toml")
    shutil.copy2(repo_root / "CHANGELOG.md", tmp_path / "CHANGELOG.md")
    env = os.environ.copy()
    env["TAG_NAME"] = "v0.3.0-alpha.1"

    proc = subprocess.run(  # nosec B603
        [sys.executable, "-c", _prerelease_validation_script()],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "### Changed" in (tmp_path / "release-notes.md").read_text(encoding="utf-8")
