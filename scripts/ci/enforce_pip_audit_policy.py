# pylint: disable=import-outside-toplevel
"""Fail closed when pip-audit findings cannot be proven below the release threshold."""

# DECISION: Governed by docs/adr/0007-deterministic-release-assurance-scripts.md.
# Keep this release policy synchronous, deterministic, and fail closed.

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

INPUT_PATH = os.environ.get("PIP_AUDIT_INPUT_PATH", "pip-audit.json")
STATUS_PATH = os.environ.get("PIP_AUDIT_STATUS_PATH", "pip-audit.status.json")
OSV_BASE = os.environ.get("OSV_BASE_URL", "https://api.osv.dev/v1/vulns/")
TIMEOUT_S = 20
_OSV_ATTEMPTS = 4
_OSV_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

CRITICAL_CVSS = 9.0
SEVERITY_ORDER = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
SEVERITY_ALIASES = {"MODERATE": "MEDIUM"}
KNOWN_SEVERITIES = frozenset({"NONE", *SEVERITY_ORDER})


@dataclass(frozen=True)
class Finding:
    """Retain the package identity and OSV aliases needed for severity resolution."""

    package: str
    version: str
    vuln_id: str
    aliases: list[str]


class AuditReportError(ValueError):
    """Raised when the audit command provenance or report is not trustworthy."""


def _validate_required_packages(required: set[str], package_names: set[str]) -> None:
    """Ensure parsed report data covers explicitly required packages."""
    if not required:
        return
    missing = sorted(required - package_names)
    if missing:
        raise AuditReportError(
            "pip-audit report is missing required packages: " + ", ".join(missing)
        )


def _load_audit_status(path: str) -> int:
    """Validate pip-audit command provenance and preserve its documented exit code."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise AuditReportError(f"cannot read audit provenance: {path}") from exc

    if not isinstance(data, dict) or data.get("tool") != "pip-audit":
        raise AuditReportError("audit provenance is missing tool=pip-audit")
    exit_code = data.get("exit_code")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int) or exit_code < 0:
        raise AuditReportError("audit provenance has an invalid exit_code")
    if exit_code >= 2:
        raise AuditReportError(f"pip-audit command failed with exit code {exit_code}")
    return exit_code


def _load_findings(path: str) -> tuple[list[Finding], set[str]]:
    """Parse report findings and normalized dependency names from one trusted snapshot."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise AuditReportError(f"cannot read pip-audit report: {path}") from exc

    if not isinstance(data, dict) or "dependencies" not in data:
        raise AuditReportError("pip-audit report must contain a dependencies list")
    dependencies = data["dependencies"]
    if not isinstance(dependencies, list):
        raise AuditReportError("pip-audit report dependencies must be a list")

    findings: list[Finding] = []
    package_names: set[str] = set()
    for dep in dependencies:
        package, version, vulnerabilities = _parse_dependency(dep)
        package_names.add(package.strip().lower())
        findings.extend(
            _parse_finding(package, version, vulnerability) for vulnerability in vulnerabilities
        )
    return findings, package_names


def _parse_dependency(dependency: object) -> tuple[str, str, list[object]]:
    """Reject malformed dependency records instead of silently omitting findings."""
    if not isinstance(dependency, dict):
        raise AuditReportError("pip-audit report contains a malformed dependency")
    package = dependency.get("name")
    version = dependency.get("version")
    vulnerabilities = dependency.get("vulns")
    if not isinstance(package, str) or not package.strip():
        raise AuditReportError("pip-audit dependency has no package name")
    if not isinstance(version, str) or not version.strip():
        raise AuditReportError(f"pip-audit dependency {package!r} has no version")
    if not isinstance(vulnerabilities, list):
        raise AuditReportError(f"pip-audit dependency {package!r} has malformed vulns")
    return package, version, vulnerabilities


def _parse_finding(package: str, version: str, vulnerability: object) -> Finding:
    """Normalize one vulnerability while retaining aliases for OSV lookup."""
    if not isinstance(vulnerability, dict):
        raise AuditReportError(f"pip-audit dependency {package!r} has malformed vuln")
    vuln_id = vulnerability.get("id")
    aliases = vulnerability.get("aliases", [])
    if not isinstance(vuln_id, str) or not vuln_id.strip():
        raise AuditReportError(f"pip-audit dependency {package!r} has vuln without id")
    if not isinstance(aliases, list) or any(not isinstance(alias, str) for alias in aliases):
        raise AuditReportError(f"pip-audit vulnerability {vuln_id!r} has malformed aliases")
    return Finding(
        package=package,
        version=version,
        vuln_id=vuln_id,
        aliases=[alias for alias in aliases if alias],
    )


def _fetch_osv(vuln_id: str) -> dict | None:
    """Fetch severity metadata over HTTPS, retrying only transient service failures."""
    url = f"{OSV_BASE}{vuln_id}"
    if urllib.parse.urlparse(url).scheme != "https":
        return None
    for attempt in range(_OSV_ATTEMPTS):
        payload, http_error = _load_osv_payload(url)
        if http_error is None:
            return payload
        if not _should_retry_osv(http_error, attempt):
            return None
        time.sleep(1.5 * (attempt + 1))
    return None


def _load_osv_payload(
    url: str,
) -> tuple[dict | None, urllib.error.HTTPError | None]:
    """Read one OSV response while the caller owns retry policy."""
    try:
        with urllib.request.urlopen(  # nosec B310
            url, timeout=TIMEOUT_S
        ) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        return None, exc
    except (
        OSError,
        TimeoutError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        urllib.error.URLError,
    ):
        return None, None
    if not isinstance(payload, dict):
        return None, None
    return payload, None


def _should_retry_osv(error: urllib.error.HTTPError, attempt: int) -> bool:
    """Limit retries to transient OSV failures while attempts remain."""
    return error.code in _OSV_RETRYABLE_STATUS_CODES and attempt < _OSV_ATTEMPTS - 1


def _severity_from_osv(osv: object) -> tuple[str | None, float | None]:
    """Prefer OSV's explicit label, otherwise derive evidence from its CVSS vectors."""
    if not isinstance(osv, dict):
        return None, None

    database_specific = osv.get("database_specific")
    database_severity = (
        database_specific.get("severity") if isinstance(database_specific, dict) else None
    )
    label = _normalized_database_label(database_severity)
    if label is not None:
        return label, None

    cvss_scores = _collect_cvss_scores(osv.get("severity", []))
    return (None, max(cvss_scores)) if cvss_scores else (None, None)


def _normalized_database_label(database_severity: object) -> str | None:
    """Normalize a non-empty OSV database severity without judging its validity."""
    if not isinstance(database_severity, str) or not database_severity.strip():
        return None
    label = database_severity.strip().upper()
    return SEVERITY_ALIASES.get(label, label)


def _collect_cvss_scores(severity: object) -> list[float]:
    """Collect every parseable CVSS base score from OSV severity metadata."""
    if not isinstance(severity, list):
        return []

    cvss_scores: list[float] = []
    for item in severity:
        if not isinstance(item, dict):
            continue
        score = item.get("score")
        if not isinstance(score, str) or not score.strip():
            continue
        cvss_score = _parse_cvss_score(score.strip())
        if cvss_score is not None:
            cvss_scores.append(cvss_score)
    return cvss_scores


def _parse_cvss_score(score: str) -> float | None:
    """Parse CVSS vectors when the optional library is present, otherwise fail closed."""
    try:
        if score.startswith("CVSS:3"):
            from cvss import CVSS3  # type: ignore[import-not-found]

            return float(CVSS3(score).scores()[0])
        if score.startswith("CVSS:2"):
            from cvss import CVSS2

            return float(CVSS2(score).scores()[0])
        return float(score)
    except ImportError, AttributeError, IndexError, TypeError, ValueError:
        return None


def _severity_label_from_cvss(base_score: float) -> str:
    """Map a CVSS base score to the policy's stable severity vocabulary."""
    # Common CVSS v3.x severity bands
    if base_score >= 9.0:
        return "CRITICAL"
    if base_score >= 7.0:
        return "HIGH"
    if base_score >= 4.0:
        return "MEDIUM"
    if base_score > 0.0:
        return "LOW"
    return "NONE"


def _classify_findings(findings: list[Finding]) -> tuple[list[str], list[str], list[str]]:
    """Group blocking and unverifiable findings while sharing OSV responses by ID."""
    osv_cache: dict[str, dict | None] = {}
    critical: list[str] = []
    high: list[str] = []
    unknown: list[str] = []

    for finding in findings:
        best_label = _resolve_severity(finding, osv_cache)
        summary = f"{finding.package}=={finding.version} ({finding.vuln_id})"
        if best_label is None:
            unknown.append(summary)
        elif best_label == "CRITICAL":
            critical.append(summary)
        elif best_label == "HIGH":
            high.append(summary)
    return critical, high, unknown


def _resolve_severity(finding: Finding, osv_cache: dict[str, dict | None]) -> str | None:
    """Select the strongest trusted label; unknown labels remain policy failures."""
    resolved_labels, resolved_cvss = _severity_evidence(finding, osv_cache)
    if any(label not in KNOWN_SEVERITIES for label in resolved_labels):
        return None
    evidence_labels = [*resolved_labels]
    evidence_labels.extend(_severity_label_from_cvss(score) for score in resolved_cvss)
    if not evidence_labels:
        return None
    return max(evidence_labels, key=lambda severity: SEVERITY_ORDER.get(severity, 0))


def _severity_evidence(
    finding: Finding,
    osv_cache: dict[str, dict | None],
) -> tuple[list[str], list[float]]:
    """Collect labels and scores across the primary advisory and every alias."""
    labels: list[str] = []
    cvss_scores: list[float] = []
    for vuln_id in (finding.vuln_id, *finding.aliases):
        if vuln_id not in osv_cache:
            osv_cache[vuln_id] = _fetch_osv(vuln_id)
        osv = osv_cache[vuln_id]
        if not osv:
            continue
        label, cvss_base = _severity_from_osv(osv)
        if label:
            labels.append(label)
        if cvss_base is not None:
            cvss_scores.append(cvss_base)
    return labels, cvss_scores


def _report_blockers(critical: list[str], high: list[str], unknown: list[str]) -> int:
    """Emit one actionable policy result, treating missing severity as blocking."""
    if critical:
        return _print_blockers("CRITICAL vulnerabilities found:", critical)
    if high:
        return _print_blockers("HIGH vulnerabilities found:", high)
    if unknown:
        return _print_blockers("Vulnerabilities with unknown severity (fail-closed):", unknown)

    print("No CRITICAL or HIGH vulnerabilities found (policy passed).")
    return 0


def _print_blockers(heading: str, blockers: list[str]) -> int:
    """Print one blocker group using the policy's stable command-line format."""
    print(heading)
    for line in blockers:
        print(f"- {line}")
    return 1


def _load_policy_inputs() -> tuple[int, list[Finding]]:
    """Load provenance, findings, and required-package scope before classification."""
    audit_exit_code = _load_audit_status(STATUS_PATH)
    findings, package_names = _load_findings(INPUT_PATH)
    required_packages = {
        item.strip().lower()
        for item in os.environ.get("PIP_AUDIT_REQUIRED_PACKAGES", "").split(",")
        if item.strip()
    }
    _validate_required_packages(required_packages, package_names)
    return audit_exit_code, findings


def main() -> int:
    """Return stable CI codes for trusted clean, blocked, and malformed audit input."""
    try:
        audit_exit_code, findings = _load_policy_inputs()
    except AuditReportError as exc:
        print(f"pip-audit policy input failure (fail-closed): {exc}", file=sys.stderr)
        return 2

    if not findings:
        if audit_exit_code != 0:
            print(
                "pip-audit returned a finding status but produced an empty report (fail-closed).",
                file=sys.stderr,
            )
            return 2
        print("pip-audit: no vulnerabilities found.")
        return 0

    # Exit code 1 is pip-audit's documented finding status. Any other
    # non-zero command status was rejected by _load_audit_status above.
    if audit_exit_code not in (0, 1):
        print(f"pip-audit command failed with exit code {audit_exit_code}", file=sys.stderr)
        return 2

    return _report_blockers(*_classify_findings(findings))


if __name__ == "__main__":
    sys.exit(main())
