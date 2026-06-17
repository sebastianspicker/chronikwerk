from __future__ import annotations

import time
from typing import Any

from zammad_pdf_archiver.config.redact import scrub_secrets_in_text

_HISTORY: list[dict[str, Any]] = []
_MAX_HISTORY = 5000


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
            "id": str(len(_HISTORY) + 1),
            "status": status,
            "ticket_id": ticket_id,
            "classification": classification,
            "message": scrub_secrets_in_text(message or ""),
            "delivery_id": delivery_id,
            "request_id": request_id,
            "created_at": time.time(),
        }
    )
    del _HISTORY[:-_MAX_HISTORY]


def read_history(
    limit: int,
    ticket_id: int | None = None,
) -> list[dict[str, Any]]:
    bounded_limit = max(0, min(int(limit), _MAX_HISTORY))
    items = [
        item for item in reversed(_HISTORY) if ticket_id is None or item["ticket_id"] == ticket_id
    ]
    return items[:bounded_limit]


def reset_for_tests() -> None:
    _HISTORY.clear()
