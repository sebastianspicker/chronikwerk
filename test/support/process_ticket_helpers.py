from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import respx
from fastapi.testclient import TestClient

from test.support.credentials import fake_credential  # pylint: disable=wrong-import-order
from test.support.settings_factory import make_settings  # pylint: disable=wrong-import-order
from zammad_pdf_archiver.adapters.storage.layout import build_filename_from_pattern
from zammad_pdf_archiver.adapters.zammad.models import TagList
from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.domain.errors import TransientError

TEST_WEBHOOK_SECRET = fake_credential("webhook")


def fixed_process_ticket_now(monkeypatch: Any, module: Any) -> datetime:
    fixed = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(module, "now_utc", lambda: fixed)
    return fixed


def expected_process_ticket_pdf_path(tmp_path: Path, settings: Settings, now: datetime) -> Path:
    filename = build_filename_from_pattern(
        settings.storage.filename_pattern,
        ticket_number="20240123",
        timestamp_utc=now.date().isoformat(),
    )
    return tmp_path / "agent" / "A" / "B" / "C" / filename


def archive_ticket_json(
    ticket_id: int = 123,
    *,
    number: str = "20240123",
    owner: str = "agent",
    fallback: str = "fallback-agent",
    archive_path: str | list[str] = "A > B > C",
) -> dict[str, object]:
    return {
        "id": ticket_id,
        "number": number,
        "title": "Example Ticket",
        "owner": {"login": owner},
        "updated_by": {"login": fallback},
        "preferences": {
            "custom_fields": {
                "archive_user_mode": "owner",
                "archive_path": archive_path,
            }
        },
    }


def archive_article_json() -> dict[str, object]:
    return {
        "id": 1,
        "created_at": "2026-02-07T11:59:00Z",
        "internal": False,
        "subject": "Hello",
        "body": "<p>Hello World</p>",
        "content_type": "text/html",
        "from": "customer@example.invalid",
        "attachments": [],
    }


def post_signed_json(
    client: TestClient, path: str, payload: Any, *, secret: str = TEST_WEBHOOK_SECRET
):
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return client.post(
        path,
        content=body,
        headers={"Content-Type": "application/json", "X-Hub-Signature": f"sha256={digest}"},
    )


async def noop_process_ticket(
    _delivery_id: object,
    _payload: object,
    _settings: object,
) -> None:
    return None


def process_ticket_settings(storage_root: Path) -> Settings:
    return make_settings(str(storage_root))


def process_ticket_payload(ticket_id: int = 321) -> dict[str, dict[str, int]]:
    return {"ticket": {"id": ticket_id}}


def process_ticket_request_payload(request_id: str, ticket_id: int = 123) -> dict[str, object]:
    return {
        "ticket": {"id": ticket_id},
        "_request_id": request_id,
        "user": {"login": "agent-from-webhook"},
    }


def register_process_ticket_fetch_routes(*, articles: list[dict[str, object]]) -> None:
    respx.get("https://zammad.example.local/api/v1/tickets/123").mock(
        return_value=httpx.Response(200, json=archive_ticket_json())
    )
    respx.get(
        "https://zammad.example.local/api/v1/tags",
        params={"object": "Ticket", "o_id": "123"},
    ).mock(return_value=httpx.Response(200, json=["pdf:sign"]))
    respx.get("https://zammad.example.local/api/v1/ticket_articles/by_ticket/123").mock(
        return_value=httpx.Response(200, json=articles)
    )


def register_process_ticket_tag_routes() -> tuple[respx.Route, respx.Route]:
    remove_tag_route = respx.post("https://zammad.example.local/api/v1/tags/remove").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    add_tag_route = respx.post("https://zammad.example.local/api/v1/tags/add").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    return remove_tag_route, add_tag_route


def register_process_ticket_article_route(
    json_body: dict[str, object] | None = None,
) -> respx.Route:
    return respx.post("https://zammad.example.local/api/v1/ticket_articles").mock(
        return_value=httpx.Response(200, json=json_body or {"id": 999})
    )


def fake_ticket(ticket_id: int, title: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=ticket_id,
        number="12345",
        title=title,
        owner=SimpleNamespace(login="owner.user"),
        updated_by=SimpleNamespace(login="agent.user"),
        preferences=SimpleNamespace(
            custom_fields={
                "archive_path": "Support > Team",
                "archive_user_mode": "owner",
            }
        ),
    )


class CapturingLog:
    def __init__(self) -> None:
        self.exception_events: list[str] = []

    def info(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def exception(self, event: str, **_kwargs: Any) -> None:
        self.exception_events.append(event)


class BaseProcessTicketClient:
    _tags: set[str] = {"pdf:sign"}
    title = "process-ticket"

    def __init__(self, **kwargs: Any) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    async def get_ticket(self, ticket_id: int) -> SimpleNamespace:
        return fake_ticket(ticket_id, self.title)

    async def list_tags(self, _ticket_id: int) -> TagList:
        return TagList(sorted(type(self)._tags))

    async def remove_tag(self, _ticket_id: int, tag: str) -> None:
        type(self)._tags.discard(tag)

    async def add_tag(self, _ticket_id: int, tag: str) -> None:
        type(self)._tags.add(tag)

    async def list_articles(self, _ticket_id: int) -> list[SimpleNamespace]:
        return []

    async def create_internal_article(
        self, _ticket_id: int, _subject: str, _body_html: str
    ) -> SimpleNamespace:
        return SimpleNamespace(id=1)


class CleanupProcessTicketClient(BaseProcessTicketClient):
    title = "cleanup"


class SerializingProcessTicketClient(BaseProcessTicketClient):
    title = "concurrency"
    _notes_written = 0

    @classmethod
    def reset(cls) -> None:
        cls._tags = {"pdf:sign"}
        cls._notes_written = 0

    async def list_tags(self, _ticket_id: int) -> TagList:
        snapshot = sorted(type(self)._tags)
        await asyncio.sleep(0.05)
        return TagList(snapshot)

    async def create_internal_article(
        self, _ticket_id: int, _subject: str, _body_html: str
    ) -> SimpleNamespace:
        type(self)._notes_written += 1
        return SimpleNamespace(id=type(self)._notes_written)


class InflightRetryProcessTicketClient(BaseProcessTicketClient):
    title = "idempotency"
    _success_notes = 0
    _error_notes = 0

    @classmethod
    def reset(cls) -> None:
        cls._tags = {"pdf:sign"}
        cls._success_notes = 0
        cls._error_notes = 0

    async def list_tags(self, _ticket_id: int) -> TagList:
        await asyncio.sleep(0.05)
        return TagList(sorted(type(self)._tags))

    async def create_internal_article(
        self, _ticket_id: int, subject: str, _body_html: str
    ) -> SimpleNamespace:
        if "archiver error" in subject:
            type(self)._error_notes += 1
        if "PDF archived" in subject:
            type(self)._success_notes += 1
        return SimpleNamespace(id=type(self)._error_notes + type(self)._success_notes)


async def no_op_apply_error(
    _client: object,
    _ticket_id: int,
    *,
    _keep_trigger: bool = True,
    _trigger_tag: str = "pdf:sign",
) -> None:
    return None


async def raise_transient_render_failure(*args: Any, **kwargs: Any) -> SimpleNamespace:
    raise TransientError("render-failed")


async def successful_render(
    _client: object,
    ticket: SimpleNamespace,
    _tags: TagList,
    _ticket_id: int,
    _settings: Settings,
) -> tuple[bytes, SimpleNamespace]:
    return b"%PDF-1.7\n%%EOF\n", SimpleNamespace(ticket=ticket)


def stored_pdf_result(tmp_path: Path) -> SimpleNamespace:
    target_path = tmp_path / "archived.pdf"
    return SimpleNamespace(
        target_path=target_path,
        sidecar_path=target_path.with_suffix(".pdf.json"),
        sha256_hex="deadbeef",
        size_bytes=42,
    )


def fake_store_ticket_files(tmp_path: Path):
    def _store_ticket_files(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        return stored_pdf_result(tmp_path)

    return _store_ticket_files


def flaky_then_successful_render():
    calls = {"n": 0}

    async def _build_and_render_pdf(
        client: object,
        ticket: SimpleNamespace,
        tags: TagList,
        ticket_id: int,
        settings: Settings,
    ) -> tuple[bytes, SimpleNamespace]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise TransientError("transient-render-failure")
        return await successful_render(client, ticket, tags, ticket_id, settings)

    return _build_and_render_pdf
