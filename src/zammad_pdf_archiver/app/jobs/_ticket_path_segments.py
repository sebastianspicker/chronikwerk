from __future__ import annotations

from typing import Any


def parse_archive_path_text(value: str) -> list[str]:
    return [part for part in (raw_part.strip() for raw_part in value.split(">")) if part]


def parse_archive_path_list(value: list[Any]) -> list[str]:
    parts: list[str] = []
    for idx, item in enumerate(value):
        item = archive_path_list_item(item, idx=idx)
        if item:
            parts.append(item)
    return parts


def archive_path_list_item(item: Any, *, idx: int) -> str:
    if not isinstance(item, str):
        raise ValueError(f"custom_fields.archive_path[{idx}] must be a string")
    return item.strip()
