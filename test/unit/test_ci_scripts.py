from __future__ import annotations

from pathlib import Path

from test.support.checks import check


def test_ci_smoke_script_checks_current_repo_layout() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "scripts" / "ci" / "smoke-test.sh"
    script_text = script.read_text(encoding="utf-8")

    required_paths = [
        "README.md",
        "pyproject.toml",
        "docs/01-architecture.md",
        "config/config.example.yaml",
        "src/zammad_pdf_archiver/templates/default/ticket.html",
        ".github/workflows/ci.yml",
    ]
    for relative_path in required_paths:
        check(
            not not (repo_root / relative_path).exists(),
            f"missing required smoke path: {relative_path}",
        )
        check(not relative_path not in script_text, f"smoke script does not check {relative_path}")
    check(not "Missing required path:" not in script_text, "assertion failed")
    check(not "OK." not in script_text, "assertion failed")


def test_makefile_qa_target_runs_smoke_test() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    makefile = (repo_root / "Makefile").read_text(encoding="utf-8")
    check(not "scripts/ci/smoke-test.sh" not in makefile, "assertion failed")


def test_makefile_has_non_artifact_churning_local_verify_target() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    makefile = (repo_root / "Makefile").read_text(encoding="utf-8")

    check(not "build-check:" not in makefile, "assertion failed")
    check(
        not "python -m build --outdir /tmp/zammad-ticket-archiver-build-local" not in makefile,
        "assertion failed",
    )
    check(not "verify-local: qa build-check" not in makefile, "assertion failed")
