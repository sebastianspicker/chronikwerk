# pylint: disable=import-outside-toplevel
from __future__ import annotations

import io
import os
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from zammad_pdf_archiver.config.settings import SigningSettings
from zammad_pdf_archiver.domain.errors import PermanentError, TransientError

# Interval (seconds) between certificate expiry re-checks for cached signers.
_CERT_CHECK_INTERVAL_SECONDS = 3600


@dataclass(frozen=True)
class _PfxMaterial:
    path: Path
    pfx_bytes: bytes
    password: bytes | None


def _missing_signing_dependency(exc: ImportError) -> PermanentError:
    return PermanentError(
        f"Signing dependencies are not installed ({exc.name}). "
        "Install zammad-pdf-archiver[signing] or disable signing.enabled."
    )


def _load_pfx(signing: SigningSettings) -> _PfxMaterial:
    pfx_path = signing.pfx_path
    if pfx_path is None:
        raise PermanentError("Missing signing material: signing.pfx_path")

    path = Path(pfx_path)
    if not path.exists() or not path.is_file():
        raise PermanentError(f"PFX file not found: {path}")

    password_secret = signing.pfx_password
    password_str = password_secret.get_secret_value() if password_secret is not None else None
    password = password_str.encode("utf-8") if password_str else None
    return _PfxMaterial(path=path, pfx_bytes=path.read_bytes(), password=password)


def _validate_cert_not_expired(pfx_bytes: bytes, password: bytes | None) -> None:
    # Import lazily to keep non-signing code paths importable without crypto deps.
    try:
        from cryptography.hazmat.primitives.serialization import pkcs12
    except ImportError as exc:
        raise _missing_signing_dependency(exc) from exc

    try:
        key, cert, _extra = pkcs12.load_key_and_certificates(pfx_bytes, password)
    except ValueError as exc:
        hint = "wrong password" if password else "missing/incorrect password"
        raise PermanentError(
            f"Failed to load PKCS#12/PFX bundle ({hint} or corrupted file)"
        ) from exc

    if key is None or cert is None:
        raise PermanentError("PKCS#12/PFX bundle must contain a private key and certificate")

    now = datetime.now(UTC)
    not_before = cert.not_valid_before_utc
    not_after = cert.not_valid_after_utc

    if now < not_before:
        raise PermanentError(f"Signing certificate is not valid before {not_before.isoformat()}")
    if now > not_after:
        raise PermanentError(f"Signing certificate expired on {not_after.isoformat()}")


@dataclass
class _CachedSigner:
    signer: Any  # signers.SimpleSigner
    pfx_mtime: float
    pfx_bytes: bytes
    password: bytes | None
    last_cert_check: float


_signer_cache_lock = threading.Lock()
_signer_cache: dict[str, _CachedSigner] = {}


def _cached_signer_for_current_mtime(
    pfx_path_str: str,
    current_mtime: float,
    now: float,
) -> tuple[Any | None, tuple[bytes, bytes | None] | None]:
    with _signer_cache_lock:
        cached = _signer_cache.get(pfx_path_str)
        if cached is None or cached.pfx_mtime != current_mtime:
            return None, None
        if now - cached.last_cert_check < _CERT_CHECK_INTERVAL_SECONDS:
            return cached.signer, None
        return None, (cached.pfx_bytes, cached.password)


def _signer_after_cert_recheck(
    pfx_path_str: str,
    current_mtime: float,
    check_material: tuple[bytes, bytes | None] | None,
) -> Any | None:
    if check_material is None:
        return None

    pfx_bytes_for_check, password_for_check = check_material
    _validate_cert_not_expired(pfx_bytes_for_check, password_for_check)
    with _signer_cache_lock:
        cached = _signer_cache.get(pfx_path_str)
        if cached is None or cached.pfx_mtime != current_mtime:
            return None
        cached.last_cert_check = time.monotonic()
        return cached.signer


def _build_signer_entry(
    pfx: _PfxMaterial,
    *,
    _pfx_path_str: str,
    current_mtime: float,
) -> tuple[_CachedSigner, Any]:
    try:
        from pyhanko.sign import signers
    except ImportError as exc:
        raise _missing_signing_dependency(exc) from exc

    _validate_cert_not_expired(pfx.pfx_bytes, pfx.password)
    try:
        signer = signers.SimpleSigner.load_pkcs12(pfx.path, passphrase=pfx.password)
    except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        raise PermanentError("Failed to initialise signer from PKCS#12/PFX bundle") from exc

    return (
        _CachedSigner(
            signer=signer,
            pfx_mtime=current_mtime,
            pfx_bytes=pfx.pfx_bytes,
            password=pfx.password,
            last_cert_check=time.monotonic(),
        ),
        signer,
    )


def _cache_new_signer(
    pfx_path_str: str,
    current_mtime: float,
    entry: _CachedSigner,
) -> Any:
    with _signer_cache_lock:
        existing = _signer_cache.get(pfx_path_str)
        if existing is not None and existing.pfx_mtime == current_mtime:
            return existing.signer
        _signer_cache[pfx_path_str] = entry
        return entry.signer


def _get_cached_signer(pfx: _PfxMaterial) -> Any:
    """Return a cached SimpleSigner, re-creating it when the PFX file changes on disk.

    Certificate expiry is re-validated at most once per hour.
    """
    pfx_path_str = str(pfx.path)
    current_mtime = os.path.getmtime(pfx_path_str)
    now = time.monotonic()

    signer, check_material = _cached_signer_for_current_mtime(pfx_path_str, current_mtime, now)
    if signer is not None:
        return signer

    signer = _signer_after_cert_recheck(pfx_path_str, current_mtime, check_material)
    if signer is not None:
        return signer

    entry, _signer = _build_signer_entry(
        pfx,
        _pfx_path_str=pfx_path_str,
        current_mtime=current_mtime,
    )
    return _cache_new_signer(pfx_path_str, current_mtime, entry)


def _classify_signing_failure(exc: Exception) -> PermanentError | TransientError:
    if isinstance(
        exc,
        httpx.TimeoutException | httpx.ConnectError | ConnectionError | OSError | TimeoutError,
    ):
        return TransientError("Failed to sign PDF due to temporary (TSA) network issue")
    return PermanentError("Failed to sign PDF")


def sign_pdf(pdf_bytes: bytes, signing: SigningSettings, *, trust_env: bool = False) -> bytes:
    """
    Sign a PDF with an (invisible) PAdES signature using a locally provided PKCS#12/PFX bundle.

    If enabled via settings, an RFC3161 TSA timestamp will be embedded (PAdES-T style).
    """
    if not isinstance(pdf_bytes, bytes | bytearray) or not pdf_bytes:
        raise ValueError("pdf_bytes must be non-empty bytes")

    pfx = _load_pfx(signing)

    # Import lazily so the rest of the service stays importable even if pyHanko isn't installed.
    try:
        from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
        from pyhanko.sign.fields import SigFieldSpec
        from pyhanko.sign.signers.pdf_signer import PdfSignatureMetadata, PdfSigner
    except ImportError as exc:
        raise _missing_signing_dependency(exc) from exc

    reason = signing.pades.reason
    location = signing.pades.location

    signer = _get_cached_signer(pfx)

    field_name = "Signature1"
    meta = PdfSignatureMetadata(field_name=field_name, reason=reason, location=location)

    timestamper = None
    if signing.timestamp.enabled:
        try:
            from zammad_pdf_archiver.adapters.signing.tsa_rfc3161 import build_timestamper
        except ImportError as exc:
            raise _missing_signing_dependency(exc) from exc

        timestamper = build_timestamper(signing, trust_env=trust_env)

    pdf_signer = PdfSigner(
        signature_meta=meta,
        signer=signer,
        timestamper=timestamper,
        new_field_spec=SigFieldSpec(
            sig_field_name=field_name,
            box=(0, 0, 0, 0),
        ),
    )

    out = io.BytesIO()
    try:
        writer = IncrementalPdfFileWriter(io.BytesIO(bytes(pdf_bytes)))
        pdf_signer.sign_pdf(writer, output=out)
    except (TransientError, PermanentError):
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise _classify_signing_failure(exc) from exc

    return out.getvalue()
