from __future__ import annotations

import time
from collections import deque
from itertools import count
from typing import Any

from zammad_pdf_archiver.config.redact import scrub_secrets_in_text

_MAX_HISTORY = 5000
_HISTORY: deque[dict[str, Any]] = deque(maxlen=_MAX_HISTORY)
_HISTORY_IDS = count(1)


def _matches_status(status: str, statuses: set[str] | None) -> bool:
    if not statuses:
        return True
    return any(status == item or status.startswith(f"{item}_") for item in statuses)


def record_history_event(
    status: str,
    ticket_id: int | None,
    classification: str | None = None,
    message: str | None = None,
    delivery_id: str | None = None,
    request_id: str | None = None,
) -> None:
    _HISTORY.append(
        {
            "id": str(next(_HISTORY_IDS)),
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
    *,
    before_id: int | None = None,
    statuses: set[str] | None = None,
) -> list[dict[str, Any]]:
    bounded_limit = max(0, min(int(limit), _MAX_HISTORY))
    items = [
        item
        for item in reversed(_HISTORY)
        if (ticket_id is None or item["ticket_id"] == ticket_id)
        and (before_id is None or int(item["id"]) < before_id)
        and _matches_status(str(item["status"]), statuses)
    ]
    return items[:bounded_limit]


def reset_for_tests() -> None:
    global _HISTORY_IDS
    _HISTORY.clear()
    _HISTORY_IDS = count(1)
