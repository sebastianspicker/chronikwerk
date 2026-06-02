"""Queue envelope data model and primitive parsing helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, overload


def _as_str(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _parse_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(_as_str(value))
    except Exception:
        return default


@overload
def _parse_int(value: Any, *, default: None) -> int | None: ...


@overload
def _parse_int(value: Any, *, default: int = 0) -> int: ...


def _parse_int(value: Any, *, default: int | None = 0) -> int | None:
    try:
        return int(_as_str(value))
    except Exception:
        return default


@dataclass(frozen=True)
class _QueueEnvelope:
    message_id: str
    payload: dict[str, Any]
    delivery_id: str | None
    attempt: int
    not_before_ts: float
    last_error: str | None
    enqueued_at: str | None = None


def _decode_envelope(message_id: Any, raw_fields: dict[Any, Any]) -> _QueueEnvelope:
    fields = {_as_str(key): value for key, value in raw_fields.items()}
    payload_raw = _as_str(fields.get("payload_json", "{}"))
    try:
        payload = json.loads(payload_raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in queue payload_json: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("payload_json is not an object")

    delivery_id_raw = _as_str(fields.get("delivery_id", "")).strip()
    last_error_raw = _as_str(fields.get("last_error", "")).strip()
    enqueued_at_raw = _as_str(fields.get("enqueued_at", "")).strip()
    return _QueueEnvelope(
        message_id=_as_str(message_id),
        payload=payload,
        delivery_id=delivery_id_raw or None,
        attempt=max(0, _parse_int(fields.get("attempt"), default=0)),
        not_before_ts=max(0.0, _parse_float(fields.get("not_before_ts"), default=0.0)),
        last_error=last_error_raw or None,
        enqueued_at=enqueued_at_raw or None,
    )
