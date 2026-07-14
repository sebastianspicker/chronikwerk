from __future__ import annotations

import builtins
import io
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import SecretStr

from test.support.signing_test_helpers import sample_pdf_bytes, write_test_pfx
from zammad_pdf_archiver.adapters.signing.sign_pdf import sign_pdf
from zammad_pdf_archiver.config.settings import SigningSettings
from zammad_pdf_archiver.domain.errors import PermanentError


def _make_settings(*, pfx_path: Path | None, pfx_password: str | None) -> SigningSettings:
    return SigningSettings(
        enabled=True,
        pfx_path=pfx_path,
        pfx_password=SecretStr(pfx_password) if pfx_password else None,
    )

def test_sign_pdf_returns_pdf_bytes(tmp_path: Path) -> None:
    pfx_path = tmp_path / "test.pfx"
    write_test_pfx(pfx_path, password="secret")

    signing = _make_settings(pfx_path=pfx_path, pfx_password="secret")
    signed = sign_pdf(sample_pdf_bytes(), signing)
    assert signed.startswith(b"%PDF-")


def test_sign_pdf_uses_pades_subfilter(tmp_path: Path) -> None:
    from pyhanko.pdf_utils.reader import PdfFileReader

    pfx_path = tmp_path / "test.pfx"
    write_test_pfx(pfx_path, password="secret")

    signed = sign_pdf(
        sample_pdf_bytes(),
        _make_settings(pfx_path=pfx_path, pfx_password="secret"),
    )

    signature = PdfFileReader(io.BytesIO(signed)).embedded_signatures[0]
    assert signature.sig_object["/SubFilter"] == "/ETSI.CAdES.detached"

def test_sign_pdf_missing_pfx_path_raises() -> None:
    signing = SigningSettings(enabled=False, pfx_path=None, pfx_password=None)
    with pytest.raises(PermanentError, match="pfx_path"):
        sign_pdf(sample_pdf_bytes(), signing)

def test_sign_pdf_missing_optional_dependency_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pfx_path = tmp_path / "test.pfx"
    pfx_path.write_bytes(b"not a real pfx")

    real_import = builtins.__import__

    def fake_import(
        name: str,
        globals_: dict[str, object] | None = None,
        locals_: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if name == "cryptography.hazmat.primitives.serialization":
            raise ModuleNotFoundError("No module named 'cryptography'", name="cryptography")
        return real_import(name, globals_, locals_, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    signing = _make_settings(pfx_path=pfx_path, pfx_password=None)

    with pytest.raises(PermanentError, match=r"zammad-pdf-archiver\[signing\]"):
        sign_pdf(sample_pdf_bytes(), signing)

def test_sign_pdf_nonexistent_pfx_raises(tmp_path: Path) -> None:
    """PFX path that does not exist raises PermanentError."""
    signing = _make_settings(pfx_path=tmp_path / "missing.pfx", pfx_password=None)
    with pytest.raises(PermanentError, match="not found"):
        sign_pdf(sample_pdf_bytes(), signing)

def test_sign_pdf_empty_bytes_raises(tmp_path: Path) -> None:
    """Empty pdf_bytes raises ValueError."""
    pfx_path = tmp_path / "test.pfx"
    write_test_pfx(pfx_path, password="pw")
    signing = _make_settings(pfx_path=pfx_path, pfx_password="pw")
    with pytest.raises(ValueError, match="pdf_bytes"):
        sign_pdf(b"", signing)

def test_sign_pdf_wrong_password_raises(tmp_path: Path) -> None:
    """Wrong PFX password causes a PermanentError about loading PKCS#12."""
    pfx_path = tmp_path / "test.pfx"
    write_test_pfx(pfx_path, password="correct")
    signing = _make_settings(pfx_path=pfx_path, pfx_password="wrong")
    with pytest.raises(PermanentError):
        sign_pdf(sample_pdf_bytes(), signing)

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
        sign_pdf(sample_pdf_bytes(), signing)

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
        sign_pdf(sample_pdf_bytes(), signing)

def test_sign_pdf_signer_cache_cert_recheck(tmp_path: Path) -> None:
    """After 1h the cert is re-validated from the cache without rebuilding the signer."""

    import zammad_pdf_archiver.adapters.signing.sign_pdf as _mod

    pfx_path = tmp_path / "valid.pfx"
    write_test_pfx(pfx_path, password="cachetest")

    signing = _make_settings(pfx_path=pfx_path, pfx_password="cachetest")

    # First call — populates the cache.
    sign_pdf(sample_pdf_bytes(), signing)

    # Backdate last_cert_check so re-check threshold is crossed.
    with _mod._signer_cache_lock:  # noqa: SLF001
        entry = _mod._signer_cache[str(pfx_path)]  # noqa: SLF001
        entry.last_cert_check -= _mod._CERT_CHECK_INTERVAL_SECONDS + 1  # noqa: SLF001

    # Second call should hit the re-check path (lines 98-114) without raising.
    signed = sign_pdf(sample_pdf_bytes(), signing)
    assert signed.startswith(b"%PDF-")
