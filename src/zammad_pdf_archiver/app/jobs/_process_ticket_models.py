from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from zammad_pdf_archiver.app.jobs.ticket_storage import StorageResult
from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.domain.errors import PermanentError, TransientError


@dataclass(frozen=True)
class _TicketJobContext:
    """Immutable per-ticket context shared by private pipeline cleanup/error paths."""

    settings: Settings
    ticket_id: int
    delivery_id: str | None
    request_id: str | None


ProcessTicketStatus = Literal[
    "processed",
    "processed_done_update_failed",
    "processed_acknowledgement_failed",
    "skipped_no_ticket_id",
    "skipped_not_triggered",
    "skipped_in_flight",
    "skipped_idempotency",
    "failed_transient",
    "failed_permanent",
]

SKIPPED_PROCESS_STATUSES: frozenset[ProcessTicketStatus] = frozenset(
    {
        "skipped_no_ticket_id",
        "skipped_not_triggered",
        "skipped_in_flight",
        "skipped_idempotency",
    }
)


@dataclass(frozen=True)
class ProcessTicketResult:
    status: ProcessTicketStatus
    ticket_id: int | None
    classification: str | None = None
    message: str = ""
    articles_capped: bool = False
    attachments_skipped: int = 0
    history_recorded: bool | None = None
    error_note_posted: bool | None = None
    error_tag_applied: bool | None = None
    lock_release_failed: bool = False


@dataclass(frozen=True)
class _PipelineErrorDetails:
    classified: TransientError | PermanentError
    classification_label: str
    message: str
    action: str
    code: str
    hint: str
    status: ProcessTicketStatus


@dataclass(frozen=True)
class _ArchiveOutcome:
    storage_result: StorageResult
    articles_capped: bool
    attachments_skipped: int
    now: datetime
