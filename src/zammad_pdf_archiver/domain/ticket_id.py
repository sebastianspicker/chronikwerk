"""Project module."""
from __future__ import annotations

from typing import Any


def _positive_ticket_id(value: int) -> int | None:
    return value if value > 0 else None


def _coerce_ticket_id_string(value: str) -> int | None:
    text = value.strip()
    if not text:
        return None
    if text.startswith("+"):
        text = text[1:]
    if not text.isdigit():
        return None
    return _positive_ticket_id(int(text))


def coerce_ticket_id(value: Any) -> int | None:
    """Implement the coerce ticket id operation."""
    if isinstance(value, bool) or value is None:
        return None

    if isinstance(value, int):
        return _positive_ticket_id(value)

    if isinstance(value, str):
        return _coerce_ticket_id_string(value)

    return None


def extract_ticket_id(payload: dict[str, Any]) -> int | None:
    """
    Extract and coerce ticket ID from a webhook payload (Bug #P1-4).
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

    # Last resort: try coercive access on 'ticket' if it's not a dict but a scalar id
    return coerce_ticket_id(ticket)
