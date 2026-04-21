"""Queue envelope data model and primitive parsing helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


def _as_str(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _parse_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(_as_str(value))
    except Exception:
        return default


def _parse_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(_as_str(value))
    except Exception:
        return default


def _merge_min_delay(current: float | None, candidate: float | None) -> float | None:
    if candidate is None or candidate <= 0:
        return current
    if current is None or candidate < current:
        return candidate
    return current


@dataclass(frozen=True)
class _QueueEnvelope:
    message_id: str
    payload: dict[str, Any]
    delivery_id: str | None
    attempt: int
    not_before_ts: float
    last_error: str | None


def _decode_envelope(message_id: Any, raw_fields: dict[Any, Any]) -> _QueueEnvelope:
    fields = {_as_str(key): value for key, value in raw_fields.items()}
    payload_raw = _as_str(fields.get("payload_json", "{}"))
    payload = json.loads(payload_raw)
    if not isinstance(payload, dict):
        raise ValueError("payload_json is not an object")

    delivery_id_raw = _as_str(fields.get("delivery_id", "")).strip()
    last_error_raw = _as_str(fields.get("last_error", "")).strip()
    return _QueueEnvelope(
        message_id=_as_str(message_id),
        payload=payload,
        delivery_id=delivery_id_raw or None,
        attempt=max(0, _parse_int(fields.get("attempt"), default=0)),
        not_before_ts=max(0.0, _parse_float(fields.get("not_before_ts"), default=0.0)),
        last_error=last_error_raw or None,
    )
