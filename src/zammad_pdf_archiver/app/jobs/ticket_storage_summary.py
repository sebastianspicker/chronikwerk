from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from zammad_pdf_archiver.domain.snapshot_models import Snapshot


def attachment_summary(
    snapshot: Snapshot,
    attachment_entries: list[dict[str, Any]],
) -> dict[str, Any] | None:
    total = 0
    metadata_only = 0
    skipped = 0
    skipped_reasons: dict[str, int] = {}

    for article in snapshot.articles:
        for att in article.attachments:
            total += 1
            if att.content is None:
                metadata_only += 1
            if att.content_omission_reason:
                skipped += 1
                skipped_reasons[att.content_omission_reason] = (
                    skipped_reasons.get(att.content_omission_reason, 0) + 1
                )

    if total == 0:
        return None

    return {
        "total": total,
        "written": len(attachment_entries),
        "metadata_only": metadata_only,
        "skipped": skipped,
        "skipped_reasons": skipped_reasons,
    }
