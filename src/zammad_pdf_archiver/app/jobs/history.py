"""Project module."""
from __future__ import annotations

import time
from collections import deque
from itertools import count
from typing import Any

from zammad_pdf_archiver.config.redact import scrub_secrets_in_text

_MAX_HISTORY = 5000


class _HistoryState:  # pylint: disable=too-few-public-methods
    """Own mutable process-local history state and its monotonic identifier source."""

    def __init__(self) -> None:
        """Implement the   init   operation."""
        self.events: deque[dict[str, Any]] = deque(maxlen=_MAX_HISTORY)
        self.ids = count(1)

    def reset(self) -> None:
        """Implement the reset operation."""
        self.events.clear()
        self.ids = count(1)


_STATE = _HistoryState()


def record_history_event(
    status: str,
    ticket_id: int | None,
    *,
    classification: str | None = None,
    message: str | None = None,
    delivery_id: str | None = None,
    request_id: str | None = None,
) -> None:
    """Implement the record history event operation."""
    _STATE.events.append(
        {
            "id": str(next(_STATE.ids)),
            "status": status,
            "ticket_id": ticket_id,
            "classification": classification,
            "message": scrub_secrets_in_text(message or ""),
            "delivery_id": delivery_id,
            "request_id": request_id,
            "created_at": time.time(),
        }
    )


def read_history(
    limit: int,
    ticket_id: int | None = None,
) -> list[dict[str, Any]]:
    """Implement the read history operation."""
    bounded_limit = max(0, min(int(limit), _MAX_HISTORY))
    items = [
        item
        for item in reversed(_STATE.events)
        if ticket_id is None or item["ticket_id"] == ticket_id
    ]
    return items[:bounded_limit]


def reset_for_tests() -> None:
    """Implement the reset for tests operation."""
    _STATE.reset()
