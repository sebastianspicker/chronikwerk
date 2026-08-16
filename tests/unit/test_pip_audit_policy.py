"""Verifies pip-audit reports are schema-checked and fail closed on unsafe input."""

from __future__ import annotations

import importlib.util
import json
import sys
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import pytest


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


def test_required_package_coverage_reads_the_report_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _write_inputs(
        tmp_path,
        {"dependencies": [{"name": "cryptography", "version": "1", "vulns": []}]},
    )
    monkeypatch.setenv("PIP_AUDIT_REQUIRED_PACKAGES", "cryptography")
    report_path = Path(module.INPUT_PATH)
    original_open = open
    report_reads = 0

    def count_report_reads(path: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal report_reads
        if Path(path) == report_path:
            report_reads += 1
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", count_report_reads)

    result = module.main()
    assert (result, report_reads) == (0, 1)


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


@pytest.mark.parametrize(
    ("osv", "expected"),
    [
        (
            {
                "database_specific": {"severity": " moderate "},
                "severity": [{"score": "9.8"}],
            },
            ("MEDIUM", None),
        ),
        (
            {
                "database_specific": {"severity": "important"},
                "severity": [{"score": "9.8"}],
            },
            ("IMPORTANT", None),
        ),
        ({"severity": [{"score": "7.0"}, {"score": "9.8"}]}, (None, 9.8)),
        ({"database_specific": {"severity": " "}, "severity": [{"score": "7.0"}]}, (None, 7.0)),
        ({"severity": [{"score": "not-a-score"}]}, (None, None)),
    ],
)
def test_severity_from_osv_prefers_database_labels_and_uses_max_cvss_score(
    osv: dict, expected: tuple[str | None, float | None]
) -> None:
    module = _policy_module()

    assert module._severity_from_osv(osv) == expected


@pytest.mark.parametrize("payload", [[], "not-an-object", 7, None])
def test_fetch_osv_rejects_non_object_json_payloads(
    payload: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _policy_module()

    monkeypatch.setattr(
        module.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: nullcontext(object()),
    )
    monkeypatch.setattr(module.json, "load", lambda _response: payload)

    assert module._fetch_osv("OSV-MALFORMED") is None


@pytest.mark.parametrize(
    "osv",
    [
        {"database_specific": []},
        {"severity": {"score": "9.8"}},
        {"severity": [None, "CVSS:3.1/AV:N", {"score": 9.8}, {"score": None}]},
        {"database_specific": {"severity": ["HIGH"]}, "severity": [{"score": False}]},
    ],
)
def test_malformed_osv_severity_data_fails_closed(osv: dict) -> None:
    module = _policy_module()
    finding = module.Finding("example", "1.0", "OSV-MALFORMED", [])
    module._fetch_osv = lambda _vuln_id: osv

    assert module._classify_findings([finding]) == ([], [], ["example==1.0 (OSV-MALFORMED)"])


def test_classification_fetches_each_primary_or_alias_id_once_including_none() -> None:
    module = _policy_module()
    fetched_ids: list[str] = []

    def fetch_osv(vuln_id: str) -> dict | None:
        fetched_ids.append(vuln_id)
        if vuln_id == "OSV-MISSING":
            return None
        return {"database_specific": {"severity": "MEDIUM"}}

    module._fetch_osv = fetch_osv
    findings = [
        module.Finding("one", "1", "OSV-ONE", ["OSV-TWO", "OSV-MISSING"]),
        module.Finding("two", "2", "OSV-TWO", ["OSV-ONE", "OSV-MISSING"]),
    ]

    assert module._classify_findings(findings) == ([], [], [])
    assert fetched_ids == ["OSV-ONE", "OSV-TWO", "OSV-MISSING"]


def test_classification_uses_strongest_label_or_cvss_across_aliases() -> None:
    module = _policy_module()
    finding = module.Finding("example", "1.0", "OSV-PRIMARY", ["CVE-ALIAS"])
    osv_by_id = {
        "OSV-PRIMARY": {"database_specific": {"severity": "MEDIUM"}},
        "CVE-ALIAS": {"severity": [{"score": "9.8"}]},
    }
    module._fetch_osv = osv_by_id.get

    assert module._classify_findings([finding]) == (
        ["example==1.0 (OSV-PRIMARY)"],
        [],
        [],
    )


def test_fetch_osv_rejects_non_https_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _policy_module()
    module.OSV_BASE = "http://api.osv.dev/v1/vulns/"

    def unexpected_request(*_args, **_kwargs) -> None:
        raise AssertionError("non-HTTPS OSV URL must not be requested")

    monkeypatch.setattr(module.urllib.request, "urlopen", unexpected_request)

    assert module._fetch_osv("OSV-ONE") is None


def test_fetch_osv_retries_transient_http_errors_and_returns_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _policy_module()
    payload = {"database_specific": {"severity": "HIGH"}}
    outcomes = [
        module.urllib.error.HTTPError("https://osv/1", 503, "unavailable", {}, None),
        module.urllib.error.HTTPError("https://osv/1", 429, "limited", {}, None),
        object(),
    ]
    requests: list[tuple[str, int]] = []
    sleeps: list[float] = []

    def urlopen(url: str, *, timeout: int):
        requests.append((url, timeout))
        outcome = outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return nullcontext(outcome)

    monkeypatch.setattr(module.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(module.json, "load", lambda _response: payload)
    monkeypatch.setattr(module.time, "sleep", sleeps.append)

    assert module._fetch_osv("OSV-ONE") == payload
    assert requests == [(f"{module.OSV_BASE}OSV-ONE", module.TIMEOUT_S)] * 3
    assert sleeps == [1.5, 3.0]


def test_fetch_osv_stops_after_terminal_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _policy_module()
    requests: list[str] = []
    sleeps: list[float] = []

    def urlopen(url: str, *, timeout: int):
        assert timeout == module.TIMEOUT_S
        requests.append(url)
        raise module.urllib.error.HTTPError(url, 404, "missing", {}, None)

    monkeypatch.setattr(module.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(module.time, "sleep", sleeps.append)

    assert module._fetch_osv("OSV-MISSING") is None
    assert requests == [f"{module.OSV_BASE}OSV-MISSING"]
    assert sleeps == []


def test_fetch_osv_stops_after_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _policy_module()
    requests: list[str] = []

    def urlopen(url: str, *, timeout: int):
        assert timeout == module.TIMEOUT_S
        requests.append(url)
        raise module.urllib.error.URLError("offline")

    monkeypatch.setattr(module.urllib.request, "urlopen", urlopen)

    assert module._fetch_osv("OSV-OFFLINE") is None
    assert requests == [f"{module.OSV_BASE}OSV-OFFLINE"]


@pytest.mark.parametrize(
    ("critical", "high", "unknown", "expected_code", "expected_output"),
    [
        (
            ["critical"],
            ["high"],
            ["unknown"],
            1,
            "CRITICAL vulnerabilities found:\n- critical\n",
        ),
        ([], ["high"], ["unknown"], 1, "HIGH vulnerabilities found:\n- high\n"),
        (
            [],
            [],
            ["unknown"],
            1,
            "Vulnerabilities with unknown severity (fail-closed):\n- unknown\n",
        ),
        (
            [],
            [],
            [],
            0,
            "No CRITICAL or HIGH vulnerabilities found (policy passed).\n",
        ),
    ],
)
def test_report_blockers_preserves_precedence_and_output(
    critical: list[str],
    high: list[str],
    unknown: list[str],
    expected_code: int,
    expected_output: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _policy_module()

    assert module._report_blockers(critical, high, unknown) == expected_code
    assert capsys.readouterr().out == expected_output


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
