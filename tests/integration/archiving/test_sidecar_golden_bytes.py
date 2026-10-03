"""Pin the exact bytes of the audit sidecar written by the storage repository."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

from chronikwerk._version import __version__
from chronikwerk.documents.models import Article, Snapshot, TicketMeta
from chronikwerk.storage.options import ArchiveStorageOptions, SigningProvenance
from chronikwerk.storage.repository import StoreTicketFilesRequest, store_ticket_files_request

_PDF = b"%PDF-1.7\n%%EOF\n"
# sha256 of _PDF, computed independently of the code under test.
_PDF_SHA256 = "1e7313ace78f0fb481a486939b4885902663102818090805515553d84e0bbfd3"
_PYTHON = sys.version.split(" ", 1)[0]


def _store(tmp_path: Path, provenance: SigningProvenance, *, title: str | None) -> Path:
    """Store a fixed archive and return the sidecar path."""
    target = tmp_path / "alice" / "Ticket-123_2026-02-07.pdf"
    sidecar = target.with_name(target.name + ".json")
    snapshot = Snapshot(
        ticket=TicketMeta(id=42, number="123", title=title),
        articles=[Article(id=1), Article(id=2)],
        articles_total=3,
        articles_omitted=1,
    )
    store_ticket_files_request(
        StoreTicketFilesRequest(
            pdf_bytes=_PDF,
            snapshot=snapshot,
            target_path=target,
            sidecar_path=sidecar,
            ticket_id=42,
            now=datetime(2026, 2, 7, 12, 0, 0, 999, tzinfo=UTC),
            storage=ArchiveStorageOptions(root=tmp_path, fsync=False, filename_pattern="x"),
            signing_provenance=provenance,
        )
    )
    return sidecar


def _golden(tmp_path: Path, *, signing: str, title: str) -> str:
    """Return the literal sidecar text with the given signing block and title."""
    return f"""{{
  "article_coverage": {{
    "complete": false,
    "included": 2,
    "omitted": 1,
    "total": 3
  }},
  "created_at": "2026-02-07T12:00:00Z",
  "service": {{
    "name": "chronikwerk",
    "python": "{_PYTHON}",
    "version": "{__version__}"
  }},
  "sha256": "{_PDF_SHA256}",
  "signing": {signing},
  "storage_path": "{tmp_path}/alice/Ticket-123_2026-02-07.pdf",
  "ticket_id": 42,
  "ticket_number": "123",
  "title": "{title}"
}}
"""


def test_unsigned_sidecar_is_byte_exact(tmp_path: Path) -> None:
    sidecar = _store(tmp_path, SigningProvenance(False, False), title="  Grüße & Ärger  ")

    expected = _golden(
        tmp_path,
        signing='{\n    "enabled": false,\n    "tsa_used": false\n  }',
        title="Grüße & Ärger",
    )
    assert sidecar.read_text(encoding="utf-8") == expected


def test_signed_sidecar_is_byte_exact_with_fingerprint(tmp_path: Path) -> None:
    provenance = SigningProvenance(True, True, certificate_fingerprint="ab:cd:ef")

    sidecar = _store(tmp_path, provenance, title="Signed")

    expected = _golden(
        tmp_path,
        signing=(
            '{\n    "cert_fingerprint": "ab:cd:ef",\n    "enabled": true,\n'
            '    "tsa_used": true\n  }'
        ),
        title="Signed",
    )
    assert sidecar.read_text(encoding="utf-8") == expected


def test_unsigned_provenance_never_records_a_fingerprint(tmp_path: Path) -> None:
    provenance = SigningProvenance(False, False, certificate_fingerprint="ab:cd:ef")

    text = _store(tmp_path, provenance, title="t").read_text(encoding="utf-8")

    assert "cert_fingerprint" not in text
