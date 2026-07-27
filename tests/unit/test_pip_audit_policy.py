"""Verifies pip-audit reports are schema-checked and fail closed on unsafe input."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


def _policy_module() -> Any:
    """Load the policy script without invoking its command-line entry point."""
    path = Path(__file__).resolve().parents[2] / "scripts/ci/enforce_pip_audit_policy.py"
    spec = importlib.util.spec_from_file_location("enforce_pip_audit_policy", path)
    if spec is None or spec.loader is None:
        raise AssertionError("unable to load pip-audit policy module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_inputs(tmp_path: Path, report: object, *, exit_code: int = 0) -> Any:
    """Write a paired pip-audit report and status fixture, then bind the policy paths."""
    report_path = tmp_path / "pip-audit.json"
    status_path = tmp_path / "pip-audit.status.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    status_path.write_text(
        json.dumps({"tool": "pip-audit", "exit_code": exit_code}), encoding="utf-8"
    )
    module = _policy_module()
    module.INPUT_PATH = str(report_path)
    module.STATUS_PATH = str(status_path)
    return module


def test_valid_empty_report_passes(tmp_path: Path) -> None:
    module = _write_inputs(tmp_path, {"dependencies": []})

    assert module.main() == 0


def test_required_package_coverage_is_enforced(tmp_path: Path, monkeypatch) -> None:
    module = _write_inputs(
        tmp_path,
        {
            "dependencies": [
                {"name": "cryptography", "version": "1", "vulns": []},
                {"name": "pyhanko", "version": "1", "vulns": []},
            ]
        },
    )
    monkeypatch.setenv(
        "PIP_AUDIT_REQUIRED_PACKAGES",
        "cryptography,pyhanko,pyhanko-certvalidator,asn1crypto",
    )

    assert module.main() == 2


def test_findings_are_evaluated_after_schema_validation(tmp_path: Path) -> None:
    module = _write_inputs(
        tmp_path,
        {
            "dependencies": [
                {
                    "name": "example",
                    "version": "1.0",
                    "vulns": [{"id": "CVE-2026-0001", "aliases": []}],
                }
            ]
        },
        exit_code=1,
    )
    module._fetch_osv = lambda _vuln_id: {"database_specific": {"severity": "HIGH"}}

    assert module.main() == 1


def test_unknown_database_severity_fails_closed(tmp_path: Path) -> None:
    module = _write_inputs(
        tmp_path,
        {
            "dependencies": [
                {
                    "name": "example",
                    "version": "1.0",
                    "vulns": [{"id": "CVE-2026-0002", "aliases": []}],
                }
            ]
        },
        exit_code=1,
    )
    module._fetch_osv = lambda _vuln_id: {"database_specific": {"severity": "IMPORTANT"}}

    assert module.main() == 1


def test_moderate_database_severity_normalizes_to_medium(tmp_path: Path) -> None:
    module = _write_inputs(
        tmp_path,
        {
            "dependencies": [
                {
                    "name": "example",
                    "version": "1.0",
                    "vulns": [{"id": "CVE-2026-0003", "aliases": []}],
                }
            ]
        },
        exit_code=1,
    )
    module._fetch_osv = lambda _vuln_id: {"database_specific": {"severity": "MODERATE"}}

    assert module.main() == 0


def test_missing_report_fails_closed(tmp_path: Path) -> None:
    module = _write_inputs(tmp_path, {"dependencies": []})
    (tmp_path / "pip-audit.json").unlink()

    assert module.main() == 2


def test_malformed_report_fails_before_policy(tmp_path: Path) -> None:
    module = _write_inputs(tmp_path, {})

    assert module.main() == 2


def test_partial_dependency_report_fails_closed(tmp_path: Path) -> None:
    module = _write_inputs(
        tmp_path,
        {"dependencies": [{"name": "example", "version": "1.0"}]},
    )

    assert module.main() == 2


def test_audit_command_failure_is_not_an_empty_success(tmp_path: Path) -> None:
    module = _write_inputs(tmp_path, {"dependencies": []}, exit_code=2)

    assert module.main() == 2
