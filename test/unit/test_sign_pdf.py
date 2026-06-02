from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr

from test.support.checks import check
from test.support.credentials import fake_credential
from test.support.signing_helpers import minimal_pdf_bytes, write_test_pfx
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
    signed = sign_pdf(minimal_pdf_bytes(), signing)
    check(not not signed.startswith(b"%PDF-"), "assertion failed")


def test_sign_pdf_missing_pfx_path_raises() -> None:
    signing = SigningSettings(enabled=False, pfx_path=None, pfx_password=None)
    with pytest.raises(PermanentError, match="pfx_path"):
        sign_pdf(minimal_pdf_bytes(), signing)


def test_sign_pdf_nonexistent_pfx_raises(tmp_path: Path) -> None:
    """PFX path that does not exist raises PermanentError."""
    signing = _make_settings(pfx_path=tmp_path / "missing.pfx", pfx_password=None)
    with pytest.raises(PermanentError, match="not found"):
        sign_pdf(minimal_pdf_bytes(), signing)


def test_sign_pdf_empty_bytes_raises(tmp_path: Path) -> None:
    """Empty pdf_bytes raises ValueError."""
    pfx_path = tmp_path / "test.pfx"
    write_test_pfx(pfx_path, password=fake_credential("pw"))
    signing = _make_settings(pfx_path=pfx_path, pfx_password=fake_credential("pw"))
    with pytest.raises(ValueError, match="pdf_bytes"):
        sign_pdf(b"", signing)


def test_sign_pdf_wrong_password_raises(tmp_path: Path) -> None:
    """Wrong PFX password causes a PermanentError about loading PKCS#12."""
    pfx_path = tmp_path / "test.pfx"
    write_test_pfx(pfx_path, password=fake_credential("correct"))
    signing = _make_settings(pfx_path=pfx_path, pfx_password=fake_credential("wrong"))
    with pytest.raises(PermanentError):
        sign_pdf(minimal_pdf_bytes(), signing)


def test_sign_pdf_expired_cert_raises(tmp_path: Path) -> None:
    """An expired signing certificate raises PermanentError."""
    pfx_path = tmp_path / "expired.pfx"
    write_test_pfx(
        pfx_path,
        password=None,
        common_name="Expired Signer",
        valid_from_days=-365,
        valid_until_days=-1,
    )

    signing = _make_settings(pfx_path=pfx_path, pfx_password=None)
    with pytest.raises(PermanentError, match="expired"):
        sign_pdf(minimal_pdf_bytes(), signing)


def test_sign_pdf_not_yet_valid_cert_raises(tmp_path: Path) -> None:
    """A cert whose not_valid_before is in the future raises PermanentError."""
    pfx_path = tmp_path / "future.pfx"
    write_test_pfx(
        pfx_path,
        password=None,
        common_name="Future Signer",
        valid_from_days=1,
        valid_until_days=365,
    )

    signing = _make_settings(pfx_path=pfx_path, pfx_password=None)
    with pytest.raises(PermanentError, match="not valid before"):
        sign_pdf(minimal_pdf_bytes(), signing)


def test_sign_pdf_validates_cert_on_each_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import zammad_pdf_archiver.adapters.signing.sign_pdf as _mod

    pfx_path = tmp_path / "valid.pfx"
    write_test_pfx(pfx_path, password=fake_credential("cachetest"))

    signing = _make_settings(pfx_path=pfx_path, pfx_password=fake_credential("cachetest"))
    validation_calls = 0
    real_validate = _mod._validate_cert_not_expired  # noqa: SLF001

    def _count_validation(pfx_bytes: bytes, password: bytes | None) -> None:
        nonlocal validation_calls
        validation_calls += 1
        real_validate(pfx_bytes, password)

    monkeypatch.setattr(_mod, "_validate_cert_not_expired", _count_validation)

    sign_pdf(minimal_pdf_bytes(), signing)
    signed = sign_pdf(minimal_pdf_bytes(), signing)

    check(not not signed.startswith(b"%PDF-"), "assertion failed")
    check(not not validation_calls == 2, "assertion failed")
