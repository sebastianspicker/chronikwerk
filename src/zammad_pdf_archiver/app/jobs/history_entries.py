from __future__ import annotations

from typing import Any

from zammad_pdf_archiver.app.jobs._queue_types import _parse_float, _parse_int


def append_matching_history_entries(
    out: list[dict[str, Any]],
    entries: list[tuple[Any, Any]],
    *,
    ticket_id: int | None,
    limit: int,
) -> None:
    for message_id, raw_fields in entries:
        item = history_item(message_id, raw_fields)
        if ticket_id is None or item["ticket_id"] == ticket_id:
            out.append(item)
            if len(out) >= limit:
                return


def history_item(message_id: Any, raw_fields: dict[Any, Any]) -> dict[str, Any]:
    fields = {str(k): v for k, v in raw_fields.items()}
    return normalize_entry(str(message_id), fields)


def normalize_entry(message_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    status = str(fields.get("status", ""))
    ticket_id = _parse_int(fields.get("ticket_id"), default=None)
    classification = str(fields.get("classification", "")).strip() or None
    message = str(fields.get("message", ""))
    delivery_id = str(fields.get("delivery_id", "")).strip() or None
    request_id = str(fields.get("request_id", "")).strip() or None
    created_at_ts = _parse_float(fields.get("created_at"), default=0.0)

    return {
        "id": message_id,
        "status": status,
        "ticket_id": ticket_id,
        "classification": classification,
        "message": message,
        "delivery_id": delivery_id,
        "request_id": request_id,
        "created_at": created_at_ts,
    }
