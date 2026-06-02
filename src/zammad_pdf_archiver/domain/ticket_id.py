from __future__ import annotations

from typing import Any


def coerce_ticket_id(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None

    if isinstance(value, int):
        return _positive_ticket_id(value)

    if isinstance(value, str):
        return _coerce_ticket_id_text(value)

    return None


def _positive_ticket_id(value: int) -> int | None:
    return value if value > 0 else None


def _coerce_ticket_id_text(value: str) -> int | None:
    text = value.strip()
    if not text:
        return None
    if text.startswith("+"):
        text = text[1:]
    if not text.isdigit():
        return None
    return _positive_ticket_id(int(text))


def extract_ticket_id(payload: dict[str, Any]) -> int | None:
    """
    Extract and coerce the ticket ID from supported webhook payload shapes.

    Checks ticket_id first, then ticket.id.
    """
    # Prefer top-level ticket_id (explicit).
    tid = coerce_ticket_id(payload.get("ticket_id"))
    if tid is not None:
        return tid

    # Fallback to nested ticket object.
    ticket = payload.get("ticket")
    if isinstance(ticket, dict):
        return coerce_ticket_id(ticket.get("id"))

    # Some manually generated payloads pass the ticket id as the `ticket` value itself.
    return coerce_ticket_id(ticket)
