from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import SecretStr

from zammad_pdf_archiver.config.settings import SigningSettings
from zammad_pdf_archiver.domain.audit import AuditRecordInput, build_audit_record


def _audit_input(
    *,
    ticket_id: int,
    ticket_number: str,
    title: str | None,
    storage_path: str,
    sha256: str,
    created_at: datetime | None = None,
) -> AuditRecordInput:
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
    from zammad_pdf_archiver._version import __version__
    assert audit["service"]["version"] == __version__
    assert audit["signing"] == {"enabled": False, "tsa_used": False}


def _write_test_pfx(path: Path, password: str) -> None:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Signer")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(private_key=key, algorithm=hashes.SHA256())
    )

    pfx = pkcs12.serialize_key_and_certificates(
        name=b"test-signer",
        key=key,
        cert=cert,
        cas=None,
        encryption_algorithm=serialization.BestAvailableEncryption(password.encode("utf-8")),
    )
    path.write_bytes(pfx)


def _cert_fingerprint_from_pfx(path: Path, password: str) -> str:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.serialization import pkcs12

    pfx_bytes = path.read_bytes()
    _key, cert, _extra = pkcs12.load_key_and_certificates(pfx_bytes, password.encode("utf-8"))
    assert cert is not None
    return cert.fingerprint(hashes.SHA256()).hex()


def test_build_audit_record_extracts_cert_fingerprint_from_pfx(tmp_path: Path) -> None:
    pfx_path = tmp_path / "test.pfx"
    _write_test_pfx(pfx_path, password="secret")

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
    )

    expected = _cert_fingerprint_from_pfx(pfx_path, password="secret")
    assert audit["signing"]["enabled"] is True
    assert audit["signing"]["cert_fingerprint"] == expected


def test_build_audit_record_cert_fingerprint_returns_none_on_error(tmp_path: Path) -> None:
    """_extract_cert_fingerprint returns None when PFX material is invalid."""
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
