from __future__ import annotations

from typing import Any


def parse_stream_entries(records: Any, *, nested: bool) -> list[tuple[Any, Any]]:
    if not isinstance(records, list):
        return []

    entries = _flatten_nested_stream_entries(records) if nested else records
    out: list[tuple[Any, Any]] = []
    for message in entries:
        entry = _message_entry(message)
        if entry is not None:
            out.append(entry)
    return out


def _flatten_nested_stream_entries(records: list[Any]) -> list[Any]:
    entries: list[Any] = []
    for record in records:
        messages = _nested_stream_messages(record)
        if messages is not None:
            entries.extend(messages)
    return entries


def _nested_stream_messages(record: Any) -> list[Any] | None:
    if not isinstance(record, (list, tuple)) or len(record) != 2:
        return None
    _stream_name, messages = record
    return messages if isinstance(messages, list) else None


def _message_entry(message: Any) -> tuple[Any, Any] | None:
    if isinstance(message, (list, tuple)) and len(message) == 2:
        return (message[0], message[1])
    return None
