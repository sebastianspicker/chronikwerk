# pylint: disable=import-outside-toplevel
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

CRITICAL_CVSS = 9.0
SEVERITY_ORDER = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}


@dataclass(frozen=True)
class Finding:
    package: str
    version: str
    vuln_id: str
    aliases: list[str]


class AuditReportError(ValueError):
    """Raised when the audit command provenance or report is not trustworthy."""


def _validate_required_packages(path: str, required: set[str]) -> None:
    if not required:
        return
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise AuditReportError(f"cannot read pip-audit report: {path}") from exc
    dependencies = data.get("dependencies") if isinstance(data, dict) else None
    names = (
        {
            dep["name"].lower()
            for dep in dependencies
            if isinstance(dep, dict) and isinstance(dep.get("name"), str)
        }
        if isinstance(dependencies, list)
        else set()
    )
    missing = sorted(required - names)
    if missing:
        raise AuditReportError(
            "pip-audit report is missing required packages: " + ", ".join(missing)
        )


def _load_audit_status(path: str) -> int:
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


def _load_findings(path: str) -> list[Finding]:
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
    for dep in dependencies:
        if not isinstance(dep, dict):
            raise AuditReportError("pip-audit report contains a malformed dependency")
        package = dep.get("name")
        version = dep.get("version")
        vulns = dep.get("vulns")
        if not isinstance(package, str) or not package.strip():
            raise AuditReportError("pip-audit dependency has no package name")
        if not isinstance(version, str) or not version.strip():
            raise AuditReportError(f"pip-audit dependency {package!r} has no version")
        if not isinstance(vulns, list):
            raise AuditReportError(f"pip-audit dependency {package!r} has malformed vulns")
        for vuln in vulns:
            if not isinstance(vuln, dict):
                raise AuditReportError(f"pip-audit dependency {package!r} has malformed vuln")
            vuln_id = vuln.get("id")
            aliases = vuln.get("aliases", [])
            if not isinstance(vuln_id, str) or not vuln_id.strip():
                raise AuditReportError(f"pip-audit dependency {package!r} has vuln without id")
            if not isinstance(aliases, list) or any(
                not isinstance(alias, str) for alias in aliases
            ):
                raise AuditReportError(f"pip-audit vulnerability {vuln_id!r} has malformed aliases")
            findings.append(
                Finding(
                    package=package,
                    version=version,
                    vuln_id=vuln_id,
                    aliases=[alias for alias in aliases if alias],
                )
            )
    return findings


def _fetch_osv(vuln_id: str) -> dict | None:
    url = f"{OSV_BASE}{vuln_id}"
    if urllib.parse.urlparse(url).scheme != "https":
        return None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT_S) as r:  # nosec B310
                return json.load(r)
        except urllib.error.HTTPError as e:
            # Retry on 429/5xx.
            if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(1.5 * (attempt + 1))
                continue
            return None
        except Exception:
            return None
    return None


def _severity_from_osv(osv: dict) -> tuple[str | None, float | None]:
    db_sev = osv.get("database_specific", {}).get("severity")
    if isinstance(db_sev, str) and db_sev.strip():
        return db_sev.strip().upper(), None

    cvss_scores: list[float] = []
    for item in osv.get("severity", []) or []:
        score = item.get("score")
        if not isinstance(score, str) or not score.strip():
            continue

        score = score.strip()
        cvss_score: float | None = None
        try:
            if score.startswith("CVSS:3"):
                from cvss import CVSS3  # type: ignore[import-not-found]

                cvss_score = float(CVSS3(score).scores()[0])
            elif score.startswith("CVSS:2"):
                from cvss import CVSS2

                cvss_score = float(CVSS2(score).scores()[0])
            else:
                cvss_score = float(score)
        except Exception:
            cvss_score = None
        if cvss_score is not None:
            cvss_scores.append(cvss_score)

    if not cvss_scores:
        return None, None

    return None, max(cvss_scores)


def _severity_label_from_cvss(base_score: float) -> str:
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


def main() -> int:
    try:
        audit_exit_code = _load_audit_status(STATUS_PATH)
        findings = _load_findings(INPUT_PATH)
        _validate_required_packages(
            INPUT_PATH,
            {
                item.strip().lower()
                for item in os.environ.get("PIP_AUDIT_REQUIRED_PACKAGES", "").split(",")
                if item.strip()
            },
        )
    except AuditReportError as exc:
        print(f"pip-audit policy input failure (fail-closed): {exc}", file=sys.stderr)
        return 2

    if not findings:
        if audit_exit_code != 0:
            print(
                "pip-audit returned a finding status but produced an empty report "
                "(fail-closed).",
                file=sys.stderr,
            )
            return 2
        print("pip-audit: no vulnerabilities found.")
        return 0

    # Exit code 1 is pip-audit's documented finding status.  Any other
    # non-zero command status was rejected by _load_audit_status above.
    if audit_exit_code not in (0, 1):
        print(f"pip-audit command failed with exit code {audit_exit_code}", file=sys.stderr)
        return 2

    osv_cache: dict[str, dict | None] = {}
    critical: list[str] = []
    high: list[str] = []
    unknown: list[str] = []

    for f in findings:
        ids = [f.vuln_id, *f.aliases]
        resolved_labels: list[str] = []
        resolved_cvss: list[float] = []

        for vid in ids:
            if vid not in osv_cache:
                osv_cache[vid] = _fetch_osv(vid)
            osv = osv_cache[vid]
            if not osv:
                continue
            label, cvss_base = _severity_from_osv(osv)
            if label:
                resolved_labels.append(label)
            if cvss_base is not None:
                resolved_cvss.append(cvss_base)

        best_label = None
        if resolved_labels:
            best_label = max(resolved_labels, key=lambda s: SEVERITY_ORDER.get(s, 0))
        elif resolved_cvss:
            best_label = _severity_label_from_cvss(max(resolved_cvss))

        if best_label is None:
            unknown.append(f"{f.package}=={f.version} ({f.vuln_id})")
        elif best_label == "CRITICAL":
            critical.append(f"{f.package}=={f.version} ({f.vuln_id})")
        elif best_label == "HIGH":
            high.append(f"{f.package}=={f.version} ({f.vuln_id})")

    if critical:
        print("CRITICAL vulnerabilities found:")
        for line in critical:
            print(f"- {line}")
        return 1
    if high:
        print("HIGH vulnerabilities found:")
        for line in high:
            print(f"- {line}")
        return 1
    if unknown:
        print("Vulnerabilities with unknown severity (fail-closed):")
        for line in unknown:
            print(f"- {line}")
        return 1

    print("No CRITICAL or HIGH vulnerabilities found (policy passed).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
