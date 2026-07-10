from __future__ import annotations

import stat
import subprocess  # nosec B404
from pathlib import Path


def test_ci_smoke_script_checks_current_repo_layout() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "scripts" / "ci" / "smoke-test.sh"

    assert script.stat().st_mode & stat.S_IXUSR, "smoke script must remain executable"

    # Fixed repository-local executable via shebang: shell=False and no user-controlled argv.
    # nosemgrep
    proc = subprocess.run(  # nosec B603
        [str(script)], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, proc.stderr
    assert "Missing required path" not in proc.stderr
    assert "OK." in proc.stdout


def test_makefile_qa_target_runs_smoke_test() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    makefile = (repo_root / "Makefile").read_text(encoding="utf-8")
    assert "scripts/ci/smoke-test.sh" in makefile


def test_coverage_policy_and_release_gate_are_aligned() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    makefile = (repo_root / "Makefile").read_text(encoding="utf-8")
    pyproject = (repo_root / "pyproject.toml").read_text(encoding="utf-8")
    ci = (repo_root / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "fail_under = 85" in pyproject
    assert "--cov-fail-under=85" in makefile
    assert "verify-core: lint" in makefile
    assert "verify: verify-core production-image-smoke test-e2e" in makefile
    assert "ci: verify" in makefile
    assert "make verify" in ci or "make verify-core" in ci
    assert "allow none collected" not in ci
    assert "test $? -eq 5" not in makefile


def test_release_gate_contains_production_and_e2e_contracts() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    makefile = (repo_root / "Makefile").read_text(encoding="utf-8")
    helper = repo_root / "scripts/ci/production_image_smoke.sh"
    assert "production-image-smoke" in makefile
    assert "test-e2e" in makefile
    assert helper.exists()
    helper_text = helper.read_text(encoding="utf-8")
    assert "Dockerfile" in helper_text
    assert "unsigned-render-ok" in helper_text
    assert "verify_production_signing.py" in helper_text


def test_production_signing_helper_generates_and_validates_ephemeral_material() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    helper = repo_root / "scripts" / "ci" / "verify_production_signing.py"

    helper_text = helper.read_text(encoding="utf-8")
    assert "TemporaryDirectory" in helper_text
    assert "serialize_key_and_certificates" in helper_text
    assert "validate_pdf_signature" in helper_text
    assert "production-signing-ok" in helper_text
