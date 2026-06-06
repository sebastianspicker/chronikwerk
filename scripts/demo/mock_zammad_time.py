from __future__ import annotations

from datetime import UTC, datetime


def iso_now() -> str:
    return datetime.now(UTC).isoformat()
