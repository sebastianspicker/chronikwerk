from __future__ import annotations

import copy
import threading
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from scripts.demo.mock_zammad_seed import article_from_seed, load_dataset, ticket_from_seed
from scripts.demo.mock_zammad_time import iso_now


class NewArticle(BaseModel):
    ticket_id: int
    subject: str = ""
    body: str = ""
    content_type: str = "text/html"
    internal: bool = True


class DemoStore:
    def __init__(self, dataset_path: Path) -> None:
        self._dataset_path = dataset_path
        self._lock = threading.Lock()
        self._dataset_template = load_dataset(dataset_path)
        self._tickets: dict[int, dict[str, Any]] = {}
        self._tags: dict[int, list[str]] = {}
        self._articles: dict[int, list[dict[str, Any]]] = {}
        self._events: list[dict[str, Any]] = []
        self._next_article_id: int = 1
        self.reset()

    def reset(self) -> dict[str, Any]:
        with self._lock:
            template = copy.deepcopy(self._dataset_template)
            self._tickets = {}
            self._tags = {}
            self._articles = {}
            self._events = []

            self._next_article_id = self._seed_tickets(template.get("tickets", []))
            self._events.append({"ts": iso_now(), "event": "reset", "tickets": len(self._tickets)})

            return {
                "status": "ok",
                "tickets": len(self._tickets),
                "seed_plan_count": len(template.get("seed_plan", [])),
            }

    def _seed_tickets(self, tickets: list[Any]) -> int:
        max_article_id = 1
        for item in tickets:
            ticket_id = int(item["id"])
            created = item.get("created_at") or iso_now()
            updated = item.get("updated_at") or created
            self._tickets[ticket_id] = ticket_from_seed(
                item,
                created=created,
                updated=updated,
            )
            self._tags[ticket_id] = [str(t) for t in item.get("tags", [])]
            max_article_id = self._seed_articles(
                ticket_id,
                item.get("articles", []),
                next_article_id=max_article_id,
            )
        return max_article_id

    def _seed_articles(
        self,
        ticket_id: int,
        articles: list[Any],
        *,
        next_article_id: int,
    ) -> int:
        normalized_articles: list[dict[str, Any]] = []
        for article in articles:
            normalized = article_from_seed(article, fallback_article_id=next_article_id)
            article_id = int(normalized["id"])
            next_article_id = max(next_article_id, article_id + 1)
            normalized_articles.append(normalized)
        self._articles[ticket_id] = normalized_articles
        return next_article_id

    def get_ticket(self, ticket_id: int) -> dict[str, Any]:
        with self._lock:
            ticket = self._tickets.get(ticket_id)
            if ticket is None:
                raise KeyError(ticket_id)
            return copy.deepcopy(ticket)

    def get_tags(self, ticket_id: int) -> list[str]:
        with self._lock:
            return list(self._tags.get(ticket_id, []))

    def set_tag(self, ticket_id: int, *, tag: str, present: bool) -> dict[str, Any]:
        with self._lock:
            if ticket_id not in self._tickets:
                raise KeyError(ticket_id)
            tags = self._tags.setdefault(ticket_id, [])
            if present:
                if tag not in tags:
                    tags.append(tag)
                action = "tag_add"
            else:
                tags = [x for x in tags if x != tag]
                self._tags[ticket_id] = tags
                action = "tag_remove"
            self._events.append(
                {
                    "ts": iso_now(),
                    "event": action,
                    "ticket_id": ticket_id,
                    "tag": tag,
                    "tags": list(tags),
                }
            )
            return {"status": "ok", "ticket_id": ticket_id, "tags": list(tags)}

    def list_articles(self, ticket_id: int) -> list[dict[str, Any]]:
        with self._lock:
            if ticket_id not in self._tickets:
                raise KeyError(ticket_id)
            return copy.deepcopy(self._articles.get(ticket_id, []))

    def add_article(self, payload: NewArticle) -> dict[str, Any]:
        with self._lock:
            if payload.ticket_id not in self._tickets:
                raise KeyError(payload.ticket_id)
            article = {
                "id": self._next_article_id,
                "created_at": iso_now(),
                "internal": bool(payload.internal),
                "subject": payload.subject,
                "body": payload.body,
                "content_type": payload.content_type,
                "from": "archiver@demo.local",
                "to": "",
                "attachments": [],
            }
            self._next_article_id += 1
            self._articles.setdefault(payload.ticket_id, []).append(article)
            self._events.append(
                {
                    "ts": iso_now(),
                    "event": "article_created",
                    "ticket_id": payload.ticket_id,
                    "article_id": article["id"],
                    "subject": payload.subject,
                }
            )
            return copy.deepcopy(article)

    def state(self) -> dict[str, Any]:
        with self._lock:
            items = []
            for ticket_id in sorted(self._tickets.keys()):
                ticket = self._tickets[ticket_id]
                items.append(
                    {
                        "ticket_id": ticket_id,
                        "number": ticket["number"],
                        "title": ticket.get("title"),
                        "tags": list(self._tags.get(ticket_id, [])),
                        "article_count": len(self._articles.get(ticket_id, [])),
                    }
                )

            return {
                "status": "ok",
                "dataset": str(self._dataset_path),
                "ticket_count": len(self._tickets),
                "tickets": items,
                "events_tail": self._events[-50:],
            }
