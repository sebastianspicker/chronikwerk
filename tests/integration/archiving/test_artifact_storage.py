"""Verify the public archive artifact and audit-sidecar workflow."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest

from chronikwerk.documents.models import Article, AttachmentMeta, Snapshot, TicketMeta
from chronikwerk.storage.options import ArchiveStorageOptions, SigningProvenance
from chronikwerk.storage.repository import StoreTicketFilesRequest, store_ticket_files_request


def _storage(root: Path) -> ArchiveStorageOptions:
    """Build storage options without durability flushing for a temporary root."""
    return ArchiveStorageOptions(root=root, fsync=False, filename_pattern="Ticket-{ticket}.pdf")


@pytest.mark.parametrize("omitted", [0, 2])
def test_store_ticket_files_writes_pdf_and_audit_coverage(tmp_path: Path, omitted: int) -> None:
    target = tmp_path / "archive" / "Ticket-10001.pdf"
    sidecar = target.with_suffix(".pdf.json")
    pdf_bytes = b"%PDF-1.7\\n%%EOF\\n"
    snapshot = Snapshot(
        ticket=TicketMeta(id=100, number="10001", title="Storage workflow"),
        articles_total=1 + omitted,
        articles_omitted=omitted,
        articles=[
            Article(
                id=10,
                attachments=[
                    AttachmentMeta(article_id=10, attachment_id=1, filename="report.pdf", size=4)
                ],
            )
        ],
    )

    result = store_ticket_files_request(
        StoreTicketFilesRequest(
            pdf_bytes=pdf_bytes,
            snapshot=snapshot,
            target_path=target,
            sidecar_path=sidecar,
            ticket_id=100,
            now=datetime(2025, 1, 1, tzinfo=UTC),
            storage=_storage(tmp_path),
            signing_provenance=SigningProvenance(enabled=False, tsa_used=False),
        )
    )

    record = json.loads(sidecar.read_text(encoding="utf-8"))
    assert target.read_bytes() == pdf_bytes
    assert result.sha256_hex == sha256(pdf_bytes).hexdigest()
    assert result.size_bytes == len(pdf_bytes)
    assert record["ticket_id"] == 100
    assert record["sha256"] == result.sha256_hex
    assert record["article_coverage"] == {
        "complete": omitted == 0,
        "included": 1,
        "omitted": omitted,
        "total": 1 + omitted,
    }
    assert "attachments" not in record
