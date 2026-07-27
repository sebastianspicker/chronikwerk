"""Verifies audit records normalize metadata and report signing evidence safely."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from pydantic import SecretStr

from chronikwerk.config.settings import SigningSettings, SigningTimestampSettings
from chronikwerk.domain.audit import AuditRecordInput, build_audit_record
from tests.support.signing_test_helpers import write_test_pfx


def _audit_input(
    *,
    ticket_id: int,
    ticket_number: str,
    title: str | None,
    storage_path: str,
    sha256: str,
    created_at: datetime | None = None,
) -> AuditRecordInput:
    """Build normalized audit input with a stable default timestamp."""
    return AuditRecordInput(
        ticket_id=ticket_id,
        ticket_number=ticket_number,
        title=title,
        created_at=created_at or datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC),
        storage_path=storage_path,
        sha256=sha256,
    )


def test_build_audit_record_normalizes_timestamp_and_title() -> None:
    created_at = datetime(2026, 2, 7, 12, 0, 0, 987654, tzinfo=UTC)
    audit = build_audit_record(
        _audit_input(
            ticket_id=123,
            ticket_number="T-123",
            title="  Hello  ",
            created_at=created_at,
            storage_path="/mnt/archive/T-123.pdf",
            sha256="deadbeef",
        ),
        signing_settings=None,
    )

    assert audit["created_at"] == "2026-02-07T12:00:00Z"
    assert audit["title"] == "Hello"
    from chronikwerk._version import __version__

    assert audit["service"]["version"] == __version__
    assert audit["signing"] == {"enabled": False, "tsa_used": False}


def test_build_audit_record_does_not_claim_tsa_without_signing() -> None:
    signing = SigningSettings.model_construct(
        enabled=False,
        timestamp=SigningTimestampSettings(enabled=True),
    )

    audit = build_audit_record(
        _audit_input(
            ticket_id=123,
            ticket_number="T-123",
            title="Timestamp-only config",
            storage_path="/mnt/archive/T-123.pdf",
            sha256="deadbeef",
        ),
        signing_settings=signing,
    )

    assert audit["signing"] == {"enabled": False, "tsa_used": False}


def _cert_fingerprint_from_pfx(path: Path, password: str) -> str:
    """Extract the SHA-256 certificate fingerprint from the test PFX."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.serialization import pkcs12

    pfx_bytes = path.read_bytes()
    _key, cert, _extra = pkcs12.load_key_and_certificates(pfx_bytes, password.encode("utf-8"))
    assert cert is not None
    return cert.fingerprint(hashes.SHA256()).hex()


def test_build_audit_record_uses_signer_provided_cert_fingerprint(tmp_path: Path) -> None:
    pfx_path = tmp_path / "test.pfx"
    write_test_pfx(pfx_path, password="secret")

    signing = SigningSettings(enabled=True, pfx_path=pfx_path, pfx_password=SecretStr("secret"))
    audit = build_audit_record(
        _audit_input(
            ticket_id=1,
            ticket_number="T1",
            title=None,
            storage_path="/mnt/archive/T1.pdf",
            sha256="00",
        ),
        signing_settings=signing,
        signing_cert_fingerprint="signer-derived-fingerprint",
    )

    assert audit["signing"]["enabled"] is True
    assert audit["signing"]["cert_fingerprint"] == "signer-derived-fingerprint"


def test_build_audit_record_does_not_read_configured_pfx(tmp_path: Path) -> None:
    """Audit data does not reopen mutable signing material."""
    bad_pfx = tmp_path / "bad.pfx"
    bad_pfx.write_bytes(b"not valid pfx")
    signing = SigningSettings.model_construct(
        enabled=True,
        pfx_path=bad_pfx,
        pfx_password=None,
    )
    audit = build_audit_record(
        _audit_input(
            ticket_id=4,
            ticket_number="T4",
            title="error test",
            storage_path="/mnt/archive/T4.pdf",
            sha256="cc",
        ),
        signing_settings=signing,
    )
    assert audit["signing"].get("cert_fingerprint") is None


def test_build_audit_record_includes_attachments_when_provided() -> None:
    """Optional attachment list is added to audit record (PRD §8.2)."""
    audit = build_audit_record(
        _audit_input(
            ticket_id=1,
            ticket_number="T1",
            title="t",
            storage_path="/mnt/archive/T1.pdf",
            sha256="ab",
        ),
        attachments=[
            {
                "storage_path": "/mnt/archive/attachments/1_10_file.txt",
                "article_id": 1,
                "attachment_id": 10,
                "filename": "file.txt",
                "sha256": "cd",
            },
        ],
    )
    assert audit["attachments"] == [
        {
            "storage_path": "/mnt/archive/attachments/1_10_file.txt",
            "article_id": 1,
            "attachment_id": 10,
            "filename": "file.txt",
            "sha256": "cd",
        },
    ]


def test_build_audit_record_includes_article_coverage() -> None:
    record = _audit_input(
        ticket_id=1,
        ticket_number="T1",
        title="coverage",
        storage_path="/mnt/archive/T1.pdf",
        sha256="ab",
    )
    record = replace(
        record,
        articles_total=10,
        articles_included=8,
        articles_omitted=2,
    )

    audit = build_audit_record(record)

    assert audit["article_coverage"] == {
        "total": 10,
        "included": 8,
        "omitted": 2,
        "complete": False,
    }
