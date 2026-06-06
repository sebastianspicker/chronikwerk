from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from zammad_pdf_archiver.domain.audit import compute_sha256
from zammad_pdf_archiver.domain.path_policy import sanitize_segment

if TYPE_CHECKING:
    from zammad_pdf_archiver.domain.snapshot_models import Article, AttachmentMeta, Snapshot


def iter_binary_attachments(snapshot: Snapshot) -> Iterator[tuple[Article, AttachmentMeta, bytes]]:
    for article in snapshot.articles:
        for att in article.attachments:
            if att.content is not None:
                yield article, att, att.content


def attachment_safe_name(article: Article, att: AttachmentMeta) -> str:
    fallback_name = f"article_{article.id}_{att.attachment_id or 0}"
    raw_name = f"{article.id}_{att.attachment_id or 0}_{att.filename or 'bin'}"
    return sanitize_segment(raw_name) or fallback_name


def attachment_audit_entry(
    article: Article,
    att: AttachmentMeta,
    content: bytes,
    storage_path: Path,
) -> dict[str, Any]:
    return {
        "storage_path": str(storage_path),
        "article_id": article.id,
        "attachment_id": att.attachment_id,
        "filename": att.filename,
        "sha256": compute_sha256(content),
    }
