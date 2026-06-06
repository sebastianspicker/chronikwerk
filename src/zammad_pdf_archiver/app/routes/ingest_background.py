from __future__ import annotations

from zammad_pdf_archiver.app.jobs.process_ticket import ProcessTicketResult


def handle_process_ticket_result(
    result: ProcessTicketResult,
    *,
    delivery_id: str | None,
    history_record_failed_total,
    log,
) -> None:
    if result.history_recorded is False:
        history_record_failed_total.inc()
        log.warning(
            "process_ticket.history_not_recorded",
            ticket_id=result.ticket_id,
            delivery_id=delivery_id,
        )
    if result.lock_release_failed:
        log.warning(
            "ingest.ticket_lock_release_failed",
            ticket_id=result.ticket_id,
            delivery_id=delivery_id,
        )
