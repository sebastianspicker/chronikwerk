"""Define typed ticket jobs and normalize ticket identifiers for process-local delivery."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class TicketJob:
    """One admitted archive request; job metadata never travels inside the webhook payload."""

    ticket_id: int
    payload: dict[str, Any]
    delivery_id: str | None = None
    request_id: str | None = None
    force_reprocess: bool = False


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
    """Normalize a route or webhook ticket identifier to a positive integer."""
    if isinstance(value, bool) or value is None:
        return None

    if isinstance(value, int):
        return _positive_ticket_id(value)

    if isinstance(value, str):
        return _coerce_ticket_id_string(value)

    return None


def extract_ticket_id(payload: dict[str, Any]) -> int | None:
    """Extract and coerce a ticket ID from a webhook payload."""
    tid = coerce_ticket_id(payload.get("ticket_id"))
    if tid is not None:
        return tid

    ticket = payload.get("ticket")
    if isinstance(ticket, dict):
        return coerce_ticket_id(ticket.get("id"))

    return coerce_ticket_id(ticket)
