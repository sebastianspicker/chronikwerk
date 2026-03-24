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
    from cryptography.hazmat.primitives.serialization import pkcs12

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
        raise PermanentError(
            f"Signing certificate is not valid before {not_before.isoformat()}"
        )
    if now > not_after:
        raise PermanentError(f"Signing certificate expired on {not_after.isoformat()}")


@dataclass
class _CachedSigner:
    signer: Any  # signers.SimpleSigner
    pfx_path: str
    pfx_mtime: float
    pfx_bytes: bytes
    password: bytes | None
    last_cert_check: float


_signer_cache_lock = threading.Lock()
_signer_cache: dict[str, _CachedSigner] = {}


def _get_cached_signer(pfx: _PfxMaterial) -> Any:
    """Return a cached SimpleSigner, re-creating it when the PFX file changes on disk.

    Certificate expiry is re-validated at most once per hour.
    """
    from pyhanko.sign import signers

    pfx_path_str = str(pfx.path)
    current_mtime = os.path.getmtime(pfx_path_str)
    now = time.monotonic()

    with _signer_cache_lock:
        cached = _signer_cache.get(pfx_path_str)
        if cached is not None and cached.pfx_mtime == current_mtime:
            # Re-validate certificate expiry once per hour.
            if now - cached.last_cert_check >= _CERT_CHECK_INTERVAL_SECONDS:
                _validate_cert_not_expired(cached.pfx_bytes, cached.password)
                cached.last_cert_check = now
            return cached.signer

    # Build outside the lock to avoid holding it during I/O.
    _validate_cert_not_expired(pfx.pfx_bytes, pfx.password)
    try:
        signer = signers.SimpleSigner.load_pkcs12(pfx.path, passphrase=pfx.password)
    except Exception as exc:  # noqa: BLE001 - surface as PermanentError with context
        raise PermanentError("Failed to initialise signer from PKCS#12/PFX bundle") from exc

    entry = _CachedSigner(
        signer=signer,
        pfx_path=pfx_path_str,
        pfx_mtime=current_mtime,
        pfx_bytes=pfx.pfx_bytes,
        password=pfx.password,
        last_cert_check=now,
    )

    with _signer_cache_lock:
        _signer_cache[pfx_path_str] = entry

    return signer


def sign_pdf(pdf_bytes: bytes, signing: SigningSettings, *, trust_env: bool = False) -> bytes:
    """
    Sign a PDF with an (invisible) PAdES signature using a locally provided PKCS#12/PFX bundle.

    If enabled via settings, an RFC3161 TSA timestamp will be embedded (PAdES-T style).
    """
    if not isinstance(pdf_bytes, (bytes, bytearray)) or not pdf_bytes:
        raise ValueError("pdf_bytes must be non-empty bytes")

    pfx = _load_pfx(signing)

    # Import lazily so the rest of the service stays importable even if pyHanko isn't installed.
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign.fields import SigFieldSpec
    from pyhanko.sign.signers.pdf_signer import PdfSignatureMetadata, PdfSigner

    reason = signing.pades.reason
    location = signing.pades.location

    signer = _get_cached_signer(pfx)

    field_name = "Signature1"
    meta = PdfSignatureMetadata(field_name=field_name, reason=reason, location=location)

    timestamper = None
    if signing.timestamp.enabled:
        from zammad_pdf_archiver.adapters.signing.tsa_rfc3161 import build_timestamper

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
    except Exception as exc:
        # P2-3: Classify by exception type instead of string matching.
        # Network/timeout errors (likely from TSA) are transient; everything else permanent.
        if isinstance(
            exc,
            (httpx.TimeoutException, httpx.ConnectError, ConnectionError, OSError, TimeoutError),
        ):
            raise TransientError("Failed to sign PDF due to temporary (TSA) network issue") from exc

        # Mapping remaining pyHanko errors to PermanentError for callers.
        raise PermanentError("Failed to sign PDF") from exc
    return out.getvalue()
