"""Shared helpers for ticket data (e.g. custom fields)."""

from __future__ import annotations

from typing import Any

from zammad_pdf_archiver.adapters.zammad.models import Ticket


def ticket_custom_fields(ticket: Ticket) -> dict[str, Any]:
    """Extract custom_fields from ticket.preferences, or return empty dict."""
    if ticket.preferences is None:
        return {}
    fields = ticket.preferences.custom_fields
    if isinstance(fields, dict):
        return fields
    return {}
