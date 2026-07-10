from __future__ import annotations

from pathlib import Path


def _workflow(name: str) -> str:
    return (Path(__file__).resolve().parents[2] / ".github/workflows" / name).read_text(
        encoding="utf-8"
    )


def test_ci_and_security_are_reusable_without_dropping_existing_triggers() -> None:
    ci = _workflow("ci.yml")
    security = _workflow("security.yml")
    assert "workflow_call:" in ci and "push:" in ci and "pull_request:" in ci
    assert "workflow_call:" in security and "push:" in security and "schedule:" in security


def test_rc_release_is_tag_only_and_gated_by_both_workflows() -> None:
    release = _workflow("rc-release.yml")
    assert '"v*-rc.*"' in release
    assert "workflow_dispatch" not in release
    assert "uses: ./.github/workflows/ci.yml" in release
    assert "uses: ./.github/workflows/security.yml" in release
    assert "needs: [ci, security]" in release


def test_rc_release_downloads_verified_dist_without_rebuilding() -> None:
    release = _workflow("rc-release.yml")
    assert "actions/download-artifact@" in release
    assert "name: dist" in release
    assert "sha256sum *.whl *.tar.gz" in release
    assert "python -m build" not in release


def test_rc_release_fails_closed_on_version_or_changelog_mismatch() -> None:
    release = _workflow("rc-release.yml")
    assert "tomllib" in release
    assert "tag/version mismatch" in release
    assert "missing exact CHANGELOG section" in release
    assert "empty CHANGELOG section" in release
