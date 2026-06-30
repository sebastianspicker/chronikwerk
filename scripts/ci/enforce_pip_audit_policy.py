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

INPUT_PATH = "pip-audit.json"
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


def _load_findings(path: str) -> list[Finding]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    findings: list[Finding] = []
    for dep in data.get("dependencies", []):
        for vuln in dep.get("vulns", []):
            findings.append(
                Finding(
                    package=str(dep.get("name", "")),
                    version=str(dep.get("version", "")),
                    vuln_id=str(vuln.get("id", "")),
                    aliases=[str(a) for a in vuln.get("aliases", []) if a],
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
                from cvss import CVSS3

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


findings = _load_findings(INPUT_PATH)
if not findings:
    print("pip-audit: no vulnerabilities found.")
    sys.exit(0)

osv_cache: dict[str, dict | None] = {}

critical: list[str] = []
high: list[str] = []
unknown: list[str] = []

for f in findings:
    ids = [f.vuln_id, *f.aliases]
    resolved_labels: list[str] = []
    resolved_cvss: list[float] = []

    for vid in ids:
        if not vid:
            continue
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
        continue

    if best_label == "CRITICAL":
        critical.append(f"{f.package}=={f.version} ({f.vuln_id})")
        continue
    if best_label == "HIGH":
        high.append(f"{f.package}=={f.version} ({f.vuln_id})")
        continue

if critical:
    print("CRITICAL vulnerabilities found:")
    for line in critical:
        print(f"- {line}")
    sys.exit(1)

if high:
    print("HIGH vulnerabilities found:")
    for line in high:
        print(f"- {line}")
    sys.exit(1)

if unknown:
    print("Vulnerabilities with unknown severity (fail-closed):")
    for line in unknown:
        print(f"- {line}")
    sys.exit(1)

print("No CRITICAL or HIGH vulnerabilities found (policy passed).")
sys.exit(0)
