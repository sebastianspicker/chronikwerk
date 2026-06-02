from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from zammad_pdf_archiver.config.settings import SigningSettings
from zammad_pdf_archiver.domain.package_version import get_package_version
from zammad_pdf_archiver.domain.time_utils import format_timestamp_utc


def compute_sha256(data: bytes) -> str:
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("data must be bytes")
    return hashlib.sha256(data).hexdigest()


def _extract_cert_fingerprint(signing_settings: SigningSettings) -> str | None:
    """
    Best-effort extraction of a signing certificate fingerprint (SHA-256 hex).
    """
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.serialization import pkcs12

        if signing_settings.pfx_path is not None:
            pfx_pwd = signing_settings.pfx_password
            password_str: str | None = pfx_pwd.get_secret_value() if pfx_pwd is not None else None
            password = password_str.encode("utf-8") if password_str else None

            pfx_bytes = Path(signing_settings.pfx_path).read_bytes()
            _key, cert, _extra = pkcs12.load_key_and_certificates(pfx_bytes, password)
            if cert is None:
                return None
            return cert.fingerprint(hashes.SHA256()).hex()

        # KEEP: this is an audit-only fingerprint fallback for legacy/constructed
        # signing settings. Validated runtime signing still requires pfx_path;
        # cert_path is not signer material.
        cert_path = signing_settings.pades.cert_path
        if cert_path is None:
            return None
        raw = Path(cert_path).read_bytes()
        if raw.lstrip().startswith(b"-----BEGIN"):
            cert = x509.load_pem_x509_certificate(raw)
        else:
            cert = x509.load_der_x509_certificate(raw)
        return cert.fingerprint(hashes.SHA256()).hex()
    except Exception:
        return None


def _get_fingerprint(signing_settings: SigningSettings) -> str | None:
    if not signing_settings.enabled:
        return None
    return _extract_cert_fingerprint(signing_settings)


@dataclass(frozen=True)
class AuditRecordInput:
    ticket_id: int
    ticket_number: str
    title: str | None
    created_at: datetime
    storage_path: str
    sha256: str
    signing_settings: SigningSettings | None = None
    service_name: str = "zammad-pdf-archiver"
    service_dist_name: str = "zammad-pdf-archiver"
    attachments: list[dict[str, Any]] | None = None
    attachment_summary: dict[str, Any] | None = None


def build_audit_record(record: AuditRecordInput) -> dict[str, Any]:
    """Build a JSON-serialisable audit record for a successfully archived ticket."""
    signing_enabled = record.signing_settings.enabled if record.signing_settings else False
    tsa_used = record.signing_settings.timestamp.enabled if record.signing_settings else False
    cert_fingerprint = (
        _get_fingerprint(record.signing_settings) if record.signing_settings else None
    )

    signing: dict[str, Any] = {"enabled": signing_enabled, "tsa_used": tsa_used}
    if cert_fingerprint:
        signing["cert_fingerprint"] = cert_fingerprint

    version = get_package_version(
        record.service_dist_name,
        fallback="unknown",
        catch_unexpected=True,
    )
    service: dict[str, Any] = {
        "name": record.service_name,
        "version": version,
        "python": sys.version.split(" ", 1)[0],
    }

    out: dict[str, Any] = {
        "ticket_id": int(record.ticket_id),
        "ticket_number": str(record.ticket_number),
        "title": (record.title or "").strip(),
        "created_at": format_timestamp_utc(record.created_at),
        "storage_path": str(record.storage_path),
        "sha256": str(record.sha256),
        "signing": signing,
        "service": service,
    }
    if record.attachments:
        out["attachments"] = record.attachments
    if record.attachment_summary:
        out["attachment_summary"] = record.attachment_summary
    return out
