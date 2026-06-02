from __future__ import annotations

from datetime import UTC, datetime, tzinfo
from typing import Any


class _FrozenDateTime:
    def __init__(self, fixed_now: datetime) -> None:
        self._fixed_now = fixed_now

    def now(self, tz: tzinfo | None = None) -> datetime:
        if tz is None:
            return self._fixed_now.replace(tzinfo=None)
        return self._fixed_now.astimezone(tz)


def freeze_process_ticket_now(
    monkeypatch: Any,
    process_ticket_module: Any,
    fixed_now: datetime,
) -> None:
    if fixed_now.tzinfo is None:
        fixed_now = fixed_now.replace(tzinfo=UTC)
    else:
        fixed_now = fixed_now.astimezone(UTC)
    monkeypatch.setattr(process_ticket_module, "datetime", _FrozenDateTime(fixed_now))
