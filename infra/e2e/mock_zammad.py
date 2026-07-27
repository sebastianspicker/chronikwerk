"""Provide the deterministic in-memory Zammad double used by Docker E2E tests."""

from __future__ import annotations

from fastapi import FastAPI, Query

app = FastAPI()

_tickets = {
    1101: {
        "id": 1101,
        "number": "E2E-1101",
        "title": "Successful ticket",
        "owner": {"login": "e2e.owner"},
        "preferences": {"custom_fields": {"archive_user_mode": "owner", "archive_path": "e2e"}},
    },
    1104: {
        "id": 1104,
        "number": "E2E-1104",
        "title": "Retry ticket",
        "owner": {"login": "e2e.owner"},
        "preferences": {"custom_fields": {"archive_user_mode": "owner", "archive_path": "e2e"}},
    },
}
_tags: dict[int, list[str]] = {1101: ["pdf:sign"], 1104: []}
_notes: dict[int, list[dict[str, str]]] = {1101: [], 1104: []}


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Expose a dependency-free readiness probe for Compose."""
    return {"status": "ok"}


@app.post("/__e2e/reset")
def reset() -> dict[str, str]:
    """Restore mutable tags and notes so each E2E run starts identically."""
    _tags[1101] = ["pdf:sign"]
    _tags[1104] = []
    _notes[1101] = []
    _notes[1104] = []
    return {"status": "reset"}


@app.get("/__e2e/state")
def state() -> dict[str, object]:
    """Return mutable fixture state for post-run behavioral assertions."""
    return {"tags": _tags, "notes": _notes}


@app.get("/api/v1/tickets/{ticket_id}")
def ticket(ticket_id: int) -> dict[str, object]:
    """Serve one of the fixed ticket payloads expected by the archiver client."""
    return _tickets[ticket_id]


@app.get("/api/v1/tags")
def tags(o_id: str, _object_type: str = Query(alias="object")) -> list[str]:
    """Match Zammad's tag lookup shape, including its ``object`` query name."""
    return _tags[int(o_id)]


@app.get("/api/v1/ticket_articles/by_ticket/{ticket_id}")
def articles(ticket_id: int) -> list[dict[str, object]]:
    """Return a stable HTML article so generated PDFs remain reproducible."""
    return [
        {
            "id": ticket_id,
            "created_at": "2025-01-01T00:00:00Z",
            "internal": False,
            "subject": "E2E article",
            "body": "<p>Fixture article</p>",
            "content_type": "text/html",
            "from": "customer@example.invalid",
            "attachments": [],
        }
    ]


@app.post("/api/v1/tags/{operation}")
def mutate_tag(operation: str, payload: dict[str, object]) -> dict[str, bool]:
    """Apply the small add/remove tag protocol exercised by the integration flow."""
    ticket_id = int(str(payload["o_id"]))
    tag = str(payload["item"])
    if operation == "add" and tag not in _tags[ticket_id]:
        _tags[ticket_id].append(tag)
    elif operation == "remove" and tag in _tags[ticket_id]:
        _tags[ticket_id].remove(tag)
    return {"success": True}


@app.post("/api/v1/ticket_articles")
def add_note(payload: dict[str, object]) -> dict[str, object]:
    """Record archive notes so the E2E verifier can prove Zammad-side effects."""
    ticket_id = int(str(payload["ticket_id"]))
    _notes[ticket_id].append({"subject": str(payload["subject"]), "body": str(payload["body"])})
    return {"id": len(_notes[ticket_id]), "internal": True, **payload}
