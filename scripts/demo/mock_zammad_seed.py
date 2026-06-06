from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.demo.mock_zammad_time import iso_now


def field_or_default(source: dict[str, Any], key: str, default: Any) -> Any:
    value = source.get(key)
    return value if value else default


def load_dataset(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("dataset must be a JSON object")
    tickets = payload.get("tickets")
    if not isinstance(tickets, list) or not tickets:
        raise ValueError("dataset.tickets must be a non-empty list")
    return payload


def ticket_from_seed(item: dict[str, Any], *, created: str, updated: str) -> dict[str, Any]:
    ticket_id = int(item["id"])
    return {
        "id": ticket_id,
        "number": str(item.get("number") or f"UNI-{ticket_id}"),
        "title": item.get("title"),
        "owner": {"login": item.get("owner_login")},
        "updated_by": {"login": item.get("updated_by_login")},
        "customer": item.get("customer") or {},
        "preferences": {
            "custom_fields": item.get("custom_fields") or {},
        },
        "created_at": created,
        "updated_at": updated,
    }


def article_from_seed(article: dict[str, Any], *, fallback_article_id: int) -> dict[str, Any]:
    article_id = int(article.get("id") or fallback_article_id)
    return {
        "id": article_id,
        "created_at": article.get("created_at") or iso_now(),
        "internal": bool(article.get("internal", False)),
        "subject": field_or_default(article, "subject", ""),
        "body": field_or_default(article, "body", ""),
        "content_type": field_or_default(article, "content_type", "text/plain"),
        "from": field_or_default(article, "from", ""),
        "to": field_or_default(article, "to", ""),
        "attachments": field_or_default(article, "attachments", []),
    }
