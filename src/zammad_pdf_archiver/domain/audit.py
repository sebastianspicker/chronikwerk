# pylint: disable=import-outside-toplevel
from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from zammad_pdf_archiver._version import __version__
from zammad_pdf_archiver.config.settings import SigningSettings
from zammad_pdf_archiver.domain.time_utils import format_timestamp_utc


def _extract_cert_fingerprint(signing_settings: SigningSettings) -> str | None:
    """
    Best-effort extraction of a signing certificate fingerprint (SHA-256 hex).
    """
    try:
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
    except (ImportError, OSError, TypeError, ValueError):
        return None
    return None


@dataclass(frozen=True)
class AuditRecordInput:
    ticket_id: int
    ticket_number: str
    title: str | None
    created_at: datetime
    storage_path: str
    sha256: str
    articles_total: int | None = None
    articles_included: int | None = None
    articles_omitted: int = 0


def build_audit_record(
    record: AuditRecordInput,
    *,
    signing_settings: SigningSettings | None = None,
    service_name: str = "zammad-pdf-archiver",
    attachments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a JSON-serialisable audit record for a successfully archived ticket."""
    signing_enabled = signing_settings.enabled if signing_settings else False
    tsa_used = signing_settings.timestamp.enabled if signing_settings else False
    cert_fingerprint = (
        _extract_cert_fingerprint(signing_settings)
        if signing_settings is not None and signing_settings.enabled
        else None
    )

    signing: dict[str, Any] = {"enabled": signing_enabled, "tsa_used": tsa_used}
    if cert_fingerprint:
        signing["cert_fingerprint"] = cert_fingerprint

    service: dict[str, Any] = {
        "name": service_name,
        "version": __version__,
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
    if record.articles_total is not None:
        out["article_coverage"] = {
            "total": record.articles_total,
            "included": record.articles_included,
            "omitted": record.articles_omitted,
            "complete": record.articles_omitted == 0,
        }
    if attachments:
        out["attachments"] = attachments
    return out
