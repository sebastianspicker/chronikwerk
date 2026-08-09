"""Verifies PDF signing output, PAdES settings, and safe failure modes."""

from __future__ import annotations

import builtins
import io
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import SecretStr

from chronikwerk.adapters.signing.sign_pdf import sign_pdf, sign_pdf_with_provenance
from chronikwerk.config.settings import SigningSettings
from chronikwerk.domain.errors import PermanentError, TransientError
from tests.support.signing_test_helpers import (
    sample_pdf_bytes,
    write_test_pfx,
    write_unencrypted_test_pfx,
)


def _make_settings(*, pfx_path: Path | None, pfx_password: str | None) -> SigningSettings:
    """Build settings for the test scenario."""
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

    with pytest.raises(PermanentError, match=r"chronikwerk\[signing\]"):
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


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (ValueError("invalid bundle"), PermanentError),
        (RuntimeError("unexpected signer defect"), RuntimeError),
    ],
)
def test_signer_initialisation_only_normalises_pkcs12_load_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    expected: type[Exception],
) -> None:
    from pyhanko.sign import signers

    import chronikwerk.adapters.signing.sign_pdf as signing_module

    monkeypatch.setattr(
        signing_module,
        "_validate_cert_not_expired",
        lambda _pfx_bytes, _password: (
            datetime.now(UTC) - timedelta(days=1),
            datetime.now(UTC) + timedelta(days=1),
        ),
    )

    def fail_to_load(*_args: object, **_kwargs: object) -> object:
        raise failure

    monkeypatch.setattr(signers.SimpleSigner, "load_pkcs12_data", fail_to_load)
    material = signing_module._PfxMaterial(  # noqa: SLF001
        path=tmp_path / "bundle.pfx",
        pfx_bytes=b"invalid",
        password=None,
    )

    with pytest.raises(expected):
        signing_module._build_signer_entry(material)  # noqa: SLF001


def test_sign_pdf_expired_cert_raises(tmp_path: Path) -> None:
    """An expired signing certificate raises PermanentError."""
    now = datetime.now(UTC)
    pfx_path = tmp_path / "expired.pfx"
    write_unencrypted_test_pfx(
        pfx_path,
        common_name="Expired Signer",
        not_valid_before=now - timedelta(days=365),
        not_valid_after=now - timedelta(days=1),
    )

    signing = _make_settings(pfx_path=pfx_path, pfx_password=None)
    with pytest.raises(PermanentError, match="expired"):
        sign_pdf(sample_pdf_bytes(), signing)


def test_sign_pdf_not_yet_valid_cert_raises(tmp_path: Path) -> None:
    """A cert whose not_valid_before is in the future raises PermanentError."""
    now = datetime.now(UTC)
    pfx_path = tmp_path / "future.pfx"
    write_unencrypted_test_pfx(
        pfx_path,
        common_name="Future Signer",
        not_valid_before=now + timedelta(days=1),
        not_valid_after=now + timedelta(days=365),
    )

    signing = _make_settings(pfx_path=pfx_path, pfx_password=None)
    with pytest.raises(PermanentError, match="not valid before"):
        sign_pdf(sample_pdf_bytes(), signing)


def test_sign_pdf_signer_cache_cert_recheck(tmp_path: Path) -> None:
    """After 1h the cert is re-validated from the cache without rebuilding the signer."""

    import chronikwerk.adapters.signing.sign_pdf as _mod

    pfx_path = tmp_path / "valid.pfx"
    write_test_pfx(pfx_path, password="cachetest")

    signing = _make_settings(pfx_path=pfx_path, pfx_password="cachetest")

    # First call populates the cache.
    sign_pdf(sample_pdf_bytes(), signing)

    # Backdate last_cert_check so re-check threshold is crossed.
    with _mod._signer_cache_lock:  # noqa: SLF001
        entry = _mod._signer_cache[str(pfx_path)]  # noqa: SLF001
        entry.last_cert_check -= _mod._CERT_CHECK_INTERVAL_SECONDS + 1  # noqa: SLF001

    # Second call should hit the re-check path (lines 98-114) without raising.
    signed = sign_pdf(sample_pdf_bytes(), signing)
    assert signed.startswith(b"%PDF-")


def test_signer_cache_rejects_certificate_expired_since_last_pfx_recheck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import chronikwerk.adapters.signing.sign_pdf as _mod

    class FrozenDatetime:
        current = datetime(2030, 1, 1, tzinfo=UTC)

        @classmethod
        def now(cls, tz: object | None = None) -> datetime:
            del tz
            return cls.current

    pfx_path = tmp_path / "cached.pfx"
    pfx = _mod._PfxMaterial(pfx_path, b"cached-pfx", b"password")  # noqa: SLF001
    entry = _mod._CachedSigner(  # noqa: SLF001
        signer=object(),
        pfx_bytes=pfx.pfx_bytes,
        password=pfx.password,
        certificate_fingerprint="cached",
        certificate_not_before=FrozenDatetime.current - timedelta(minutes=1),
        certificate_not_after=FrozenDatetime.current + timedelta(minutes=30),
        last_cert_check=_mod.time.monotonic(),
    )
    with _mod._signer_cache_lock:  # noqa: SLF001
        _mod._signer_cache[str(pfx_path)] = entry  # noqa: SLF001

    monkeypatch.setattr(_mod, "datetime", FrozenDatetime)
    monkeypatch.setattr(
        _mod,
        "_build_signer_entry",
        lambda _pfx: pytest.fail("expired cached signer must not be rebuilt"),
    )
    FrozenDatetime.current += timedelta(minutes=31)

    with pytest.raises(PermanentError, match="expired"):
        _mod._get_cached_signer(pfx)  # noqa: SLF001


def test_signer_cache_reloads_rotated_pfx_when_mtime_is_preserved(tmp_path: Path) -> None:
    pfx_path = tmp_path / "rotated.pfx"
    write_test_pfx(pfx_path, password="cachetest")
    signing = _make_settings(pfx_path=pfx_path, pfx_password="cachetest")

    first = sign_pdf_with_provenance(sample_pdf_bytes(), signing)
    original_stat = pfx_path.stat()
    write_test_pfx(pfx_path, password="cachetest")
    os.utime(pfx_path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))

    second = sign_pdf_with_provenance(sample_pdf_bytes(), signing)

    assert first.certificate_fingerprint != second.certificate_fingerprint
    assert second.pdf_bytes.startswith(b"%PDF-")


def test_signer_rejects_bundle_without_private_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cryptography.hazmat.primitives.serialization import pkcs12

    import chronikwerk.adapters.signing.sign_pdf as signing_module

    monkeypatch.setattr(
        pkcs12,
        "load_key_and_certificates",
        lambda _data, _password: (None, object(), ()),
    )

    with pytest.raises(PermanentError, match="private key and certificate"):
        signing_module._validate_cert_not_expired(b"not-a-pfx", None)  # noqa: SLF001


def test_signer_cache_returns_current_valid_entry_without_rebuilding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import chronikwerk.adapters.signing.sign_pdf as signing_module

    now = datetime.now(UTC)
    material = signing_module._PfxMaterial(tmp_path / "cached.pfx", b"pfx", None)  # noqa: SLF001
    cached = signing_module._CachedSigner(  # noqa: SLF001
        signer=object(),
        pfx_bytes=b"pfx",
        password=None,
        certificate_fingerprint="cached",
        certificate_not_before=now - timedelta(minutes=1),
        certificate_not_after=now + timedelta(minutes=1),
        last_cert_check=signing_module.time.monotonic(),
    )
    with signing_module._signer_cache_lock:  # noqa: SLF001
        signing_module._signer_cache[str(material.path)] = cached  # noqa: SLF001
    monkeypatch.setattr(
        signing_module,
        "_build_signer_entry",
        lambda _material: pytest.fail("fresh cached signer must be reused"),
    )

    assert signing_module._get_cached_signer(material) is cached  # noqa: SLF001


def test_signer_recheck_uses_no_entry_when_cache_changes_during_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import chronikwerk.adapters.signing.sign_pdf as signing_module

    now = datetime.now(UTC)
    material = signing_module._PfxMaterial(tmp_path / "raced.pfx", b"pfx", None)  # noqa: SLF001
    cached = signing_module._CachedSigner(  # noqa: SLF001
        signer=object(),
        pfx_bytes=b"pfx",
        password=None,
        certificate_fingerprint="cached",
        certificate_not_before=now - timedelta(minutes=1),
        certificate_not_after=now + timedelta(minutes=1),
        last_cert_check=0,
    )
    with signing_module._signer_cache_lock:  # noqa: SLF001
        signing_module._signer_cache[str(material.path)] = cached  # noqa: SLF001

    def recheck_and_rotate(_bytes: bytes, _password: bytes | None) -> tuple[datetime, datetime]:
        with signing_module._signer_cache_lock:  # noqa: SLF001
            signing_module._signer_cache.pop(str(material.path), None)  # noqa: SLF001
        return now - timedelta(minutes=1), now + timedelta(minutes=1)

    monkeypatch.setattr(signing_module, "_validate_cert_not_expired", recheck_and_rotate)

    assert (
        signing_module._signer_after_cert_recheck(  # noqa: SLF001
            str(material.path), material, (material.pfx_bytes, material.password)
        )
        is None
    )


def test_signer_cache_preserves_existing_equivalent_entry(tmp_path: Path) -> None:
    import chronikwerk.adapters.signing.sign_pdf as signing_module

    now = datetime.now(UTC)
    path = str(tmp_path / "concurrent.pfx")
    existing = signing_module._CachedSigner(  # noqa: SLF001
        signer=object(),
        pfx_bytes=b"same",
        password=b"secret",
        certificate_fingerprint="existing",
        certificate_not_before=now - timedelta(minutes=1),
        certificate_not_after=now + timedelta(minutes=1),
        last_cert_check=0,
    )
    replacement = signing_module._CachedSigner(  # noqa: SLF001
        signer=object(),
        pfx_bytes=b"same",
        password=b"secret",
        certificate_fingerprint="replacement",
        certificate_not_before=now - timedelta(minutes=1),
        certificate_not_after=now + timedelta(minutes=1),
        last_cert_check=0,
    )
    with signing_module._signer_cache_lock:  # noqa: SLF001
        signing_module._signer_cache[path] = existing  # noqa: SLF001

    assert signing_module._cache_new_signer(path, replacement) is existing  # noqa: SLF001


@pytest.mark.parametrize(
    ("failure", "expected"),
    [(OSError("tsa offline"), TransientError), (ValueError("bad PDF"), PermanentError)],
)
def test_signature_write_classifies_network_and_document_failures(
    failure: Exception, expected: type[Exception]
) -> None:
    import chronikwerk.adapters.signing.sign_pdf as signing_module

    class FailingSigner:
        def sign_pdf(self, _writer: object, *, output: io.BytesIO) -> None:
            del output
            raise failure

    with pytest.raises(expected):
        signing_module._apply_pdf_signature(  # noqa: SLF001
            b"%PDF",
            pdf_signer=FailingSigner(),
            writer_type=lambda _source: object(),
        )
