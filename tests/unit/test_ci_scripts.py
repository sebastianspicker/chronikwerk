"""Checks CI scripts, Make targets, and workflows against the release contract."""

from __future__ import annotations

import json
import re
import stat
import subprocess
import sys
from pathlib import Path

import yaml


def _assert_command_succeeds(proc: subprocess.CompletedProcess[str], marker: str) -> None:
    """Keep subprocess success and marker checks identical across CI gates."""
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert marker in proc.stdout


def test_ci_smoke_script_checks_current_repo_layout() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "scripts" / "ci" / "smoke-test.sh"

    assert script.stat().st_mode & stat.S_IXUSR, "smoke script must remain executable"

    # Execute via the script's Bash shebang; the script uses Bash-only syntax.
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


def test_brand_identity_gate_rejects_stale_public_names() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    checker = repo_root / "scripts" / "ci" / "check_brand_identity.py"
    proc = subprocess.run(  # nosec B603
        [sys.executable, str(checker)],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    _assert_command_succeeds(proc, "brand-identity-check: OK")


def test_makefile_clean_preserves_the_project_virtualenv() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    makefile = (repo_root / "Makefile").read_text(encoding="utf-8")
    clean_target = makefile.split("\nclean:\n", maxsplit=1)[1]

    assert clean_target.count("-path './.venv'") == 2
    assert clean_target.count("-path './venv'") == 2


def test_playwright_fixture_uses_runtime_valid_secrets() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    config = (repo_root / "playwright.config.ts").read_text(encoding="utf-8")

    for name in ("ZAMMAD__WEBHOOK_HMAC_SECRET", "ADMIN__ACCESS_TOKEN"):
        match = re.search(rf"{name}: '([^']+)'", config)
        assert match is not None, f"missing Playwright fixture value: {name}"
        assert len(match.group(1)) >= 32, f"Playwright fixture value is too short: {name}"


def test_codacy_runtime_matches_the_supported_python_line() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    config = yaml.safe_load((repo_root / ".codacy.yaml").read_text(encoding="utf-8"))

    assert config["runtimes"] == ["python@3.14.6"]


def test_workflows_pin_python_3146() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    workflows = [
        repo_root / ".github" / "workflows" / name
        for name in ("ci.yml", "prerelease.yml", "security.yml")
    ]

    for workflow in workflows:
        text = workflow.read_text(encoding="utf-8")
        assert 'python-version: "3.14.6"' in text, workflow.name
        assert 'python-version: "3.12"' not in text, workflow.name


def test_ci_installs_locked_frontend_toolchain() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    ci = (repo_root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert 'node-version: "24.18.0"' in ci
    assert "make frontend-install" in ci
    assert "cache-dependency-path: package-lock.json" in ci


def test_frontend_toolchain_is_pinned_and_build_check_is_non_mutating() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    package = json.loads((repo_root / "package.json").read_text(encoding="utf-8"))

    assert package["devDependencies"]["typescript"] == "7.0.2"
    assert package["devDependencies"]["jscpd"] == "5.0.12"
    assert package["devDependencies"]["@types/node"].startswith("24.")
    assert "esbuild" in package["devDependencies"]
    assert "esbuild" in package["scripts"]["build:admin-js"]
    assert "frontend/admin.ts" in package["scripts"]["build:admin-js"]
    assert "--bundle" in package["scripts"]["build:admin-js"]
    assert "build:admin-css" in package["scripts"]["build:admin"]
    assert "build:admin-js" in package["scripts"]["build:admin"]
    assert "cp " not in package["scripts"]["build:admin"]
    assert "cp " not in package["scripts"]["build:admin-js"]
    assert "cp " not in package["scripts"]["build:admin-css"]


def test_frontend_lock_matches_pins_and_includes_native_packages() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    package = json.loads((repo_root / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((repo_root / "package-lock.json").read_text(encoding="utf-8"))
    locked_packages = lock["packages"]

    assert lock["lockfileVersion"] == 3
    assert locked_packages[""]["devDependencies"] == package["devDependencies"]

    for parent, prefix in (
        ("typescript", "@typescript/typescript-"),
        ("jscpd", "jscpd-"),
    ):
        optional_dependencies = locked_packages[f"node_modules/{parent}"]["optionalDependencies"]
        expected_names = {f"node_modules/{name}" for name in optional_dependencies}
        actual_names = {
            name for name in locked_packages if name.startswith(f"node_modules/{prefix}")
        }

        assert actual_names == expected_names
        for dependency, version in optional_dependencies.items():
            entry = locked_packages[f"node_modules/{dependency}"]
            assert entry["version"] == version
            assert entry["optional"] is True
            assert entry["resolved"].startswith("https://registry.npmjs.org/")
            assert entry["integrity"].startswith("sha512-")


def test_frontend_install_requires_the_lockfile() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    makefile = (repo_root / "Makefile").read_text(encoding="utf-8")
    target = makefile.split("\nfrontend-install:\n", maxsplit=1)[1].split("\n\n", maxsplit=1)[0]

    assert (repo_root / "package-lock.json").is_file()
    assert "$(NPM) ci --ignore-scripts" in target
    assert "$(NPM) install --ignore-scripts" not in target


def test_frontend_sources_are_typescript_only() -> None:
    repo_root = Path(__file__).resolve().parents[2]

    assert (repo_root / "frontend" / "admin.ts").is_file()
    assert (repo_root / "playwright.config.ts").is_file()
    assert (repo_root / "tests" / "browser" / "admin.spec.ts").is_file()
    assert not (repo_root / "playwright.config.js").exists()
    assert not list((repo_root / "tests" / "browser").glob("*.js"))


def test_docs_check_validates_required_inventory_and_links() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    checker = repo_root / "scripts" / "ci" / "check_docs.py"
    proc = subprocess.run(  # nosec B603
        [sys.executable, str(checker)],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    _assert_command_succeeds(proc, "docs-check: OK")


def test_screenshot_renderer_is_repository_owned_and_non_browser_claims_are_explicit() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    makefile = (repo_root / "Makefile").read_text(encoding="utf-8")
    renderer = repo_root / "scripts" / "docs" / "render_admin_screenshots.py"
    screenshot_notes = (repo_root / "docs" / "screenshots" / "README.md").read_text(
        encoding="utf-8"
    )

    assert "docs-screenshots:" in makefile
    assert "scripts/docs/render_admin_screenshots.py" in makefile
    assert renderer.is_file()
    assert "not browser" in screenshot_notes
    assert "not" in screenshot_notes and "accessibility" in screenshot_notes


def test_screenshot_split_modules_import_independently() -> None:
    """Split renderer helpers must not depend on a partially initialized facade."""
    repo_root = Path(__file__).resolve().parents[2]
    docs_dir = repo_root / "scripts" / "docs"
    script = (
        "import importlib, sys; "
        f"sys.path.insert(0, {str(docs_dir)!r}); "
        "importlib.import_module(sys.argv[1])"
    )

    for module in (
        "source",
        "screenshot_rendering",
        "render_admin_screenshots_part3",
        "render_admin_screenshots",
    ):
        proc = subprocess.run(  # nosec B603
            [sys.executable, "-c", script, module],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr


def test_screenshot_renderer_avoids_the_repository_tests_namespace() -> None:
    """A pre-imported repository tests package must not shadow renderer helpers."""
    repo_root = Path(__file__).resolve().parents[2]
    docs_dir = repo_root / "scripts" / "docs"
    script = (
        "import sys; "
        "import tests; "
        f"sys.path.insert(0, {str(docs_dir)!r}); "
        "import render_admin_screenshots; "
        "assert render_admin_screenshots.render.__module__ == 'screenshot_rendering'"
    )
    proc = subprocess.run(  # nosec B603
        [sys.executable, "-c", script],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_screenshot_provenance_includes_split_helpers_and_settings_sources() -> None:
    """Every renderer helper and extracted settings source must invalidate screenshots."""
    repo_root = Path(__file__).resolve().parents[2]
    docs_dir = repo_root / "scripts" / "docs"
    script = (
        "import sys; "
        f"sys.path.insert(0, {str(docs_dir)!r}); "
        "import source; "
        "paths = source._source_paths(); "
        "hashes = source._source_hashes(); "
        "relative_paths = [path.relative_to(source.REPO_ROOT).as_posix() for path in paths]; "
        "assert relative_paths == list(hashes); "
        "assert all("
        "hashes[relative] == source._sha256(source.REPO_ROOT / relative) "
        "for relative in relative_paths"
        "); "
        "print('\\n'.join(relative_paths))"
    )
    proc = subprocess.run(  # nosec B603
        [sys.executable, "-c", script],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    paths = set(proc.stdout.splitlines())

    assert {
        "scripts/ci/ci_1_helper.py",
        "scripts/docs/render_admin_screenshots.py",
        "scripts/docs/render_admin_screenshots_part3.py",
        "scripts/docs/source.py",
        "scripts/docs/screenshot_rendering.py",
        "src/chronikwerk/config/settings.py",
        "src/chronikwerk/config/_settings_sections.py",
        "src/chronikwerk/config/_settings_signing.py",
        "src/chronikwerk/config/_settings_zammad.py",
    } <= paths


def test_coverage_policy_and_release_gate_are_aligned() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    makefile = (repo_root / "Makefile").read_text(encoding="utf-8")
    pyproject = (repo_root / "pyproject.toml").read_text(encoding="utf-8")
    ci = (repo_root / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "fail_under = 85" in pyproject
    assert "--cov-fail-under=85" in makefile
    assert "verify-core: lint" in makefile
    assert "frontend-check" in makefile
    assert "complexity" in makefile
    assert "-C 10 -L 80" in makefile
    assert "duplication" in makefile
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
