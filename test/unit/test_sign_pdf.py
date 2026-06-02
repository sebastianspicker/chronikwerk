from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import SecretStr

from test.support.checks import check
from test.support.credentials import fake_credential
from zammad_pdf_archiver.adapters.signing.sign_pdf import sign_pdf
from zammad_pdf_archiver.config.settings import SigningSettings
from zammad_pdf_archiver.domain.errors import PermanentError


def _minimal_pdf_bytes() -> bytes:
    # Minimal valid PDF with a single blank page.
    # Offsets are calculated to build a correct xref table.
    parts: list[bytes] = []
    parts.append(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")

    offsets: list[int] = [0]

    def add_obj(obj_num: int, body: bytes) -> None:
        offsets.append(sum(len(p) for p in parts))
        parts.append(f"{obj_num} 0 obj\n".encode("ascii"))
        parts.append(body)
        parts.append(b"\nendobj\n")

    add_obj(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    add_obj(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    add_obj(
        3,
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R >>",
    )
    add_obj(4, b"<< /Length 0 >>\nstream\n\nendstream")

    xref_start = sum(len(p) for p in parts)
    parts.append(b"xref\n")
    parts.append(b"0 5\n")
    parts.append(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        parts.append(f"{off:010d} 00000 n \n".encode("ascii"))

    parts.append(b"trailer\n")
    parts.append(b"<< /Size 5 /Root 1 0 R >>\n")
    parts.append(b"startxref\n")
    parts.append(f"{xref_start}\n".encode("ascii"))
    parts.append(b"%%EOF\n")
    return b"".join(parts)


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


def _make_settings(*, pfx_path: Path | None, pfx_password: str | None) -> SigningSettings:
    return SigningSettings(
        enabled=True,
        pfx_path=pfx_path,
        pfx_password=SecretStr(pfx_password) if pfx_password else None,
    )


def test_sign_pdf_returns_pdf_bytes(tmp_path: Path) -> None:
    pfx_path = tmp_path / "test.pfx"
    _write_test_pfx(pfx_path, password=fake_credential("secret"))

    signing = _make_settings(pfx_path=pfx_path, pfx_password=fake_credential("secret"))
    signed = sign_pdf(_minimal_pdf_bytes(), signing)
    check(not not signed.startswith(b"%PDF-"), "assertion failed")


def test_sign_pdf_missing_pfx_path_raises() -> None:
    signing = SigningSettings(enabled=False, pfx_path=None, pfx_password=None)
    with pytest.raises(PermanentError, match="pfx_path"):
        sign_pdf(_minimal_pdf_bytes(), signing)


def test_sign_pdf_nonexistent_pfx_raises(tmp_path: Path) -> None:
    """PFX path that does not exist raises PermanentError."""
    signing = _make_settings(pfx_path=tmp_path / "missing.pfx", pfx_password=None)
    with pytest.raises(PermanentError, match="not found"):
        sign_pdf(_minimal_pdf_bytes(), signing)


def test_sign_pdf_empty_bytes_raises(tmp_path: Path) -> None:
    """Empty pdf_bytes raises ValueError."""
    pfx_path = tmp_path / "test.pfx"
    _write_test_pfx(pfx_path, password=fake_credential("pw"))
    signing = _make_settings(pfx_path=pfx_path, pfx_password=fake_credential("pw"))
    with pytest.raises(ValueError, match="pdf_bytes"):
        sign_pdf(b"", signing)


def test_sign_pdf_wrong_password_raises(tmp_path: Path) -> None:
    """Wrong PFX password causes a PermanentError about loading PKCS#12."""
    pfx_path = tmp_path / "test.pfx"
    _write_test_pfx(pfx_path, password=fake_credential("correct"))
    signing = _make_settings(pfx_path=pfx_path, pfx_password=fake_credential("wrong"))
    with pytest.raises(PermanentError):
        sign_pdf(_minimal_pdf_bytes(), signing)


def test_sign_pdf_expired_cert_raises(tmp_path: Path) -> None:
    """An expired signing certificate raises PermanentError."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Expired Signer")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=365))
        .not_valid_after(now - timedelta(days=1))  # expired yesterday
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(private_key=key, algorithm=hashes.SHA256())
    )
    pfx_bytes = pkcs12.serialize_key_and_certificates(
        name=b"expired",
        key=key,
        cert=cert,
        cas=None,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pfx_path = tmp_path / "expired.pfx"
    pfx_path.write_bytes(pfx_bytes)

    signing = _make_settings(pfx_path=pfx_path, pfx_password=None)
    with pytest.raises(PermanentError, match="expired"):
        sign_pdf(_minimal_pdf_bytes(), signing)


def test_sign_pdf_not_yet_valid_cert_raises(tmp_path: Path) -> None:
    """A cert whose not_valid_before is in the future raises PermanentError."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Future Signer")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now + timedelta(days=1))  # valid only tomorrow
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(private_key=key, algorithm=hashes.SHA256())
    )
    pfx_bytes = pkcs12.serialize_key_and_certificates(
        name=b"future",
        key=key,
        cert=cert,
        cas=None,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pfx_path = tmp_path / "future.pfx"
    pfx_path.write_bytes(pfx_bytes)

    signing = _make_settings(pfx_path=pfx_path, pfx_password=None)
    with pytest.raises(PermanentError, match="not valid before"):
        sign_pdf(_minimal_pdf_bytes(), signing)


def test_sign_pdf_validates_cert_on_each_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import zammad_pdf_archiver.adapters.signing.sign_pdf as _mod

    pfx_path = tmp_path / "valid.pfx"
    _write_test_pfx(pfx_path, password=fake_credential("cachetest"))

    signing = _make_settings(pfx_path=pfx_path, pfx_password=fake_credential("cachetest"))
    validation_calls = 0
    real_validate = _mod._validate_cert_not_expired  # noqa: SLF001

    def _count_validation(pfx_bytes: bytes, password: bytes | None) -> None:
        nonlocal validation_calls
        validation_calls += 1
        real_validate(pfx_bytes, password)

    monkeypatch.setattr(_mod, "_validate_cert_not_expired", _count_validation)

    sign_pdf(_minimal_pdf_bytes(), signing)
    signed = sign_pdf(_minimal_pdf_bytes(), signing)

    check(not not signed.startswith(b"%PDF-"), "assertion failed")
    check(not not validation_calls == 2, "assertion failed")
