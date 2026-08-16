"""Define structured audit records written alongside archived tickets."""

# pylint: disable=import-outside-toplevel
from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from chronikwerk._version import __version__
from chronikwerk.config.settings import SigningSettings
from chronikwerk.domain.time_utils import format_timestamp_utc


@dataclass(frozen=True)
class AuditRecordInput:
    """Capture the values required to create an archival audit record."""

    ticket_id: int
    ticket_number: str
    title: str | None
    created_at: datetime
    storage_path: str
    sha256: str
    articles_total: int | None = None
    articles_included: int | None = None
    articles_omitted: int = 0


def _signing_evidence(
    signing_settings: SigningSettings | None,
    signing_cert_fingerprint: str | None,
) -> dict[str, Any]:
    signing_enabled = signing_settings.enabled if signing_settings else False
    tsa_used = (
        signing_enabled and signing_settings is not None and signing_settings.timestamp.enabled
    )

    signing: dict[str, Any] = {"enabled": signing_enabled, "tsa_used": tsa_used}
    if signing_enabled and signing_cert_fingerprint:
        signing["cert_fingerprint"] = signing_cert_fingerprint
    return signing


def _article_coverage(record: AuditRecordInput) -> dict[str, int | bool | None]:
    return {
        "total": record.articles_total,
        "included": record.articles_included,
        "omitted": record.articles_omitted,
        "complete": record.articles_omitted == 0,
    }


def build_audit_record(
    record: AuditRecordInput,
    *,
    signing_settings: SigningSettings | None = None,
    signing_cert_fingerprint: str | None = None,
    service_name: str = "chronikwerk",
    attachments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a JSON-serialisable audit record for a successfully archived ticket."""
    signing = _signing_evidence(signing_settings, signing_cert_fingerprint)

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
        out["article_coverage"] = _article_coverage(record)
    if attachments:
        out["attachments"] = attachments
    return out
