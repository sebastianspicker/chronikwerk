from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import SecretStr

from test.support.checks import check
from test.support.credentials import fake_credential
from zammad_pdf_archiver.config.settings import SigningSettings
from zammad_pdf_archiver.domain.audit import AuditRecordInput, build_audit_record, compute_sha256


def test_compute_sha256_matches_hashlib() -> None:
    expected = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    check(not not compute_sha256(b"abc") == expected, "assertion failed")


def test_compute_sha256_rejects_non_bytes() -> None:
    with pytest.raises(TypeError, match="data must be bytes"):
        compute_sha256("abc")  # type: ignore[arg-type]


def _audit_record_input(**overrides: object) -> AuditRecordInput:
    values = {
        "ticket_id": 123,
        "ticket_number": "T-123",
        "title": "Example",
        "created_at": datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC),
        "storage_path": "/mnt/archive/T-123.pdf",
        "sha256": "deadbeef",
    }
    values.update(overrides)
    return AuditRecordInput(**values)  # type: ignore[arg-type]


def test_build_audit_record_normalizes_timestamp_and_title() -> None:
    created_at = datetime(2026, 2, 7, 12, 0, 0, 987654, tzinfo=UTC)
    audit = build_audit_record(
        _audit_record_input(
            title="  Hello  ",
            created_at=created_at,
            signing_settings=None,
            service_dist_name="definitely-not-an-installed-dist-name",
        )
    )

    check(not not audit["created_at"] == "2026-02-07T12:00:00Z", "assertion failed")
    check(not not audit["title"] == "Hello", "assertion failed")
    check(not not audit["service"]["version"] == "unknown", "assertion failed")
    check(not not audit["signing"] == {"enabled": False, "tsa_used": False}, "assertion failed")


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
    if cert is None:
        raise AssertionError("assertion failed")
    return cert.fingerprint(hashes.SHA256()).hex()


def test_build_audit_record_extracts_cert_fingerprint_from_pfx(tmp_path: Path) -> None:
    pfx_path = tmp_path / "test.pfx"
    _write_test_pfx(pfx_path, password=fake_credential("secret"))

    signing = SigningSettings(enabled=True, pfx_path=pfx_path, pfx_password=SecretStr("secret"))
    audit = build_audit_record(
        _audit_record_input(
            ticket_id=1,
            ticket_number="T1",
            title=None,
            storage_path="/mnt/archive/T1.pdf",
            sha256="00",
            signing_settings=signing,
            service_dist_name="definitely-not-an-installed-dist-name",
        )
    )

    expected = _cert_fingerprint_from_pfx(pfx_path, password=fake_credential("secret"))
    check(not audit["signing"]["enabled"] is not True, "assertion failed")
    check(not not audit["signing"]["cert_fingerprint"] == expected, "assertion failed")


def test_build_audit_record_cert_fingerprint_from_pem_cert_path(tmp_path: Path) -> None:
    """_extract_cert_fingerprint reads a PEM cert from pades.cert_path."""
    from datetime import timedelta

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    from zammad_pdf_archiver.config.settings import SigningPadesSettings

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "PEM Signer")])
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
    pem_bytes = cert.public_bytes(serialization.Encoding.PEM)
    cert_path = tmp_path / "signer.pem"
    cert_path.write_bytes(pem_bytes)

    signing = SigningSettings.model_construct(
        enabled=True,
        pfx_path=None,
        pfx_password=None,
        pades=SigningPadesSettings(cert_path=cert_path),
    )
    audit = build_audit_record(
        _audit_record_input(
            ticket_id=2,
            ticket_number="T2",
            title="pem test",
            storage_path="/mnt/archive/T2.pdf",
            sha256="ff",
            signing_settings=signing,
        )
    )

    expected_fp = cert.fingerprint(hashes.SHA256()).hex()
    check(not not audit["signing"]["cert_fingerprint"] == expected_fp, "assertion failed")


def test_build_audit_record_cert_fingerprint_from_der_cert_path(tmp_path: Path) -> None:
    """_extract_cert_fingerprint reads a DER cert from pades.cert_path."""
    from datetime import timedelta

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    from zammad_pdf_archiver.config.settings import SigningPadesSettings

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "DER Signer")])
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
    der_bytes = cert.public_bytes(serialization.Encoding.DER)
    cert_path = tmp_path / "signer.der"
    cert_path.write_bytes(der_bytes)

    signing = SigningSettings.model_construct(
        enabled=True,
        pfx_path=None,
        pfx_password=None,
        pades=SigningPadesSettings(cert_path=cert_path),
    )
    audit = build_audit_record(
        _audit_record_input(
            ticket_id=3,
            ticket_number="T3",
            title="der test",
            storage_path="/mnt/archive/T3.pdf",
            sha256="ee",
            signing_settings=signing,
        )
    )

    expected_fp = cert.fingerprint(hashes.SHA256()).hex()
    check(not not audit["signing"]["cert_fingerprint"] == expected_fp, "assertion failed")


def test_build_audit_record_cert_fingerprint_returns_none_on_error(tmp_path: Path) -> None:
    """_extract_cert_fingerprint returns None when cert_path is invalid."""
    from zammad_pdf_archiver.config.settings import SigningPadesSettings

    bad_cert = tmp_path / "bad.pem"
    bad_cert.write_bytes(b"not a valid certificate")

    signing = SigningSettings.model_construct(
        enabled=True,
        pfx_path=None,
        pfx_password=None,
        pades=SigningPadesSettings(cert_path=bad_cert),
    )
    audit = build_audit_record(
        _audit_record_input(
            ticket_id=4,
            ticket_number="T4",
            title="error test",
            storage_path="/mnt/archive/T4.pdf",
            sha256="cc",
            signing_settings=signing,
        )
    )

    # When fingerprint extraction fails, the key should be absent or None.
    check(not audit["signing"].get("cert_fingerprint") is not None, "assertion failed")


def test_build_audit_record_includes_attachments_when_provided() -> None:
    """Optional attachment list is added to audit record."""
    audit = build_audit_record(
        _audit_record_input(
            ticket_id=1,
            ticket_number="T1",
            title="t",
            storage_path="/mnt/archive/T1.pdf",
            sha256="ab",
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
    )
    check(
        not not audit["attachments"]
        == [
            {
                "storage_path": "/mnt/archive/attachments/1_10_file.txt",
                "article_id": 1,
                "attachment_id": 10,
                "filename": "file.txt",
                "sha256": "cd",
            }
        ],
        "assertion failed",
    )
