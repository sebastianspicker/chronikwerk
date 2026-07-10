from __future__ import annotations

import builtins
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import SecretStr

from test.support.credentials import fake_credential  # pylint: disable=wrong-import-order
from test.support.signing_test_helpers import (  # pylint: disable=wrong-import-order
    sample_pdf_bytes,
    write_test_pfx,
)
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
    write_test_pfx(pfx_path, password=fake_credential("secret"))

    signing = _make_settings(pfx_path=pfx_path, pfx_password=fake_credential("secret"))
    signed = sign_pdf(sample_pdf_bytes(), signing)
    assert signed.startswith(b"%PDF-")

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
    write_test_pfx(pfx_path, password=fake_credential("short-password"))
    signing = _make_settings(pfx_path=pfx_path, pfx_password=fake_credential("short-password"))
    with pytest.raises(ValueError, match="pdf_bytes"):
        sign_pdf(b"", signing)

def test_sign_pdf_wrong_password_raises(tmp_path: Path) -> None:
    """Wrong PFX password causes a PermanentError about loading PKCS#12."""
    pfx_path = tmp_path / "test.pfx"
    write_test_pfx(pfx_path, password=fake_credential("correct-password"))
    signing = _make_settings(pfx_path=pfx_path, pfx_password=fake_credential("wrong-password"))
    with pytest.raises(PermanentError):
        sign_pdf(sample_pdf_bytes(), signing)

def test_sign_pdf_expired_cert_raises(tmp_path: Path) -> None:
    """An expired signing certificate raises PermanentError."""
    pfx_path = tmp_path / "expired.pfx"
    write_test_pfx(
        pfx_path,
        password=None,
        common_name="Expired Signer",
        valid_from=timedelta(days=-365),
        valid_until=timedelta(days=-1),
    )

    signing = _make_settings(pfx_path=pfx_path, pfx_password=None)
    with pytest.raises(PermanentError, match="expired"):
        sign_pdf(sample_pdf_bytes(), signing)

def test_sign_pdf_not_yet_valid_cert_raises(tmp_path: Path) -> None:
    """A cert whose not_valid_before is in the future raises PermanentError."""
    pfx_path = tmp_path / "future.pfx"
    write_test_pfx(
        pfx_path,
        password=None,
        common_name="Future Signer",
        valid_from=timedelta(days=1),
        valid_until=timedelta(days=365),
    )

    signing = _make_settings(pfx_path=pfx_path, pfx_password=None)
    with pytest.raises(PermanentError, match="not valid before"):
        sign_pdf(sample_pdf_bytes(), signing)

def test_sign_pdf_signer_cache_cert_recheck(tmp_path: Path) -> None:
    """After 1h the cert is re-validated from the cache without rebuilding the signer."""

    # pylint: disable-next=import-outside-toplevel
    import zammad_pdf_archiver.adapters.signing.sign_pdf as _mod  # cache internals are test-only

    pfx_path = tmp_path / "valid.pfx"
    write_test_pfx(pfx_path, password=fake_credential("cache-password"))

    signing = _make_settings(pfx_path=pfx_path, pfx_password=fake_credential("cache-password"))

    # First call — populates the cache.
    sign_pdf(sample_pdf_bytes(), signing)

    # Backdate last_cert_check so re-check threshold is crossed.
    # pylint: disable=protected-access  # test cache recheck path
    with _mod._signer_cache_lock:
        entry = _mod._signer_cache[str(pfx_path)]
        entry.last_cert_check -= _mod._CERT_CHECK_INTERVAL_SECONDS + 1
    # pylint: enable=protected-access

    # Second call should hit the re-check path (lines 98-114) without raising.
    signed = sign_pdf(sample_pdf_bytes(), signing)
    assert signed.startswith(b"%PDF-")
