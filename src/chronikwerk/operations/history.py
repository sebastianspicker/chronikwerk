"""Maintain bounded in-memory job history owned by one application instance."""

from __future__ import annotations

import time
from collections import deque
from itertools import count
from typing import Any

from chronikwerk.redaction import scrub_secrets_in_text

_DEFAULT_MAX_ENTRIES = 5000


def _matches_status(status: str, statuses: set[str] | None) -> bool:
    if not statuses:
        return True
    return any(status == item or status.startswith(f"{item}_") for item in statuses)


def _matches_history_filters(
    item: dict[str, Any],
    *,
    ticket_id: int | None,
    before_id: int | None,
    statuses: set[str] | None,
) -> bool:
    """Return whether a history item matches all optional operator filters."""
    if ticket_id is not None and item["ticket_id"] != ticket_id:
        return False
    if before_id is not None and int(item["id"]) >= before_id:
        return False
    return _matches_status(str(item["status"]), statuses)


class JobHistory:
    """Bounded, volatile operator history; one instance per application process."""

    def __init__(self, *, max_entries: int = _DEFAULT_MAX_ENTRIES) -> None:
        self._max_entries = max_entries
        self._entries: deque[dict[str, Any]] = deque(maxlen=max_entries)
        self._ids = count(1)

    def record(
        self,
        status: str,
        ticket_id: int | None,
        classification: str | None = None,
        message: str | None = None,
        delivery_id: str | None = None,
        request_id: str | None = None,
    ) -> None:
        """Record one bounded, non-secret history event for operators."""
        self._entries.append(
            {
                "id": str(next(self._ids)),
                "status": status,
                "ticket_id": ticket_id,
                "classification": classification,
                "message": scrub_secrets_in_text(message or ""),
                "delivery_id": delivery_id,
                "request_id": request_id,
                "created_at": time.time(),
            }
        )

    def read(
        self,
        limit: int,
        ticket_id: int | None = None,
        *,
        before_id: int | None = None,
        statuses: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return the newest matching entries, newest first."""
        bounded_limit = max(0, min(int(limit), self._max_entries))
        if bounded_limit == 0:
            return []

        items: list[dict[str, Any]] = []
        for item in reversed(self._entries):
            if _matches_history_filters(
                item,
                ticket_id=ticket_id,
                before_id=before_id,
                statuses=statuses,
            ):
                items.append(item)
                if len(items) == bounded_limit:
                    break
        return items
