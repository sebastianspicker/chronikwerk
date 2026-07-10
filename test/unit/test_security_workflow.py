from __future__ import annotations

from pathlib import Path


def test_signing_audit_uses_local_environment_and_requires_package_coverage() -> None:
    workflow = (Path(__file__).resolve().parents[2] / ".github/workflows/security.yml").read_text(
        encoding="utf-8"
    )
    assert "pip-audit --local -f json" in workflow
    assert (
        "PIP_AUDIT_REQUIRED_PACKAGES=cryptography,pyhanko,pyhanko-certvalidator,asn1crypto"
        in workflow
    )
    assert "pip-audit -f json -s osv" in workflow
    assert "pip-audit --local -f json -s osv" in workflow
