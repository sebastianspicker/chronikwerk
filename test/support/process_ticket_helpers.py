from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from test.support.settings_factory import make_settings
from zammad_pdf_archiver.adapters.zammad.models import TagList
from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.domain.errors import TransientError

TEST_WEBHOOK_SECRET = "test-webhook-secret"


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
    delivery_id: object,
    payload: object,
    settings: object,
) -> None:
    return None


def process_ticket_settings(storage_root: Path) -> Settings:
    return make_settings(str(storage_root))


def process_ticket_payload(ticket_id: int = 321) -> dict[str, dict[str, int]]:
    return {"ticket": {"id": ticket_id}}


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

    def info(self, *args: Any, **kwargs: Any) -> None:
        return None

    def exception(self, event: str, **kwargs: Any) -> None:
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

    async def list_tags(self, ticket_id: int) -> TagList:  # noqa: ARG002
        return TagList(sorted(type(self)._tags))

    async def remove_tag(self, ticket_id: int, tag: str) -> None:  # noqa: ARG002
        type(self)._tags.discard(tag)

    async def add_tag(self, ticket_id: int, tag: str) -> None:  # noqa: ARG002
        type(self)._tags.add(tag)

    async def list_articles(self, ticket_id: int) -> list[SimpleNamespace]:  # noqa: ARG002
        return []

    async def create_internal_article(
        self, ticket_id: int, subject: str, body_html: str  # noqa: ARG002
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

    async def list_tags(self, ticket_id: int) -> TagList:  # noqa: ARG002
        snapshot = sorted(type(self)._tags)
        await asyncio.sleep(0.05)
        return TagList(snapshot)

    async def create_internal_article(
        self, ticket_id: int, subject: str, body_html: str  # noqa: ARG002
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

    async def list_tags(self, ticket_id: int) -> TagList:  # noqa: ARG002
        await asyncio.sleep(0.05)
        return TagList(sorted(type(self)._tags))

    async def create_internal_article(
        self, ticket_id: int, subject: str, body_html: str  # noqa: ARG002
    ) -> SimpleNamespace:
        if "archiver error" in subject:
            type(self)._error_notes += 1
        if "PDF archived" in subject:
            type(self)._success_notes += 1
        return SimpleNamespace(id=type(self)._error_notes + type(self)._success_notes)


async def no_op_apply_error(
    client: object,
    ticket_id: int,
    *,
    keep_trigger: bool = True,
    trigger_tag: str = "pdf:sign",
) -> None:
    return None


async def raise_transient_render_failure(*args: Any, **kwargs: Any) -> SimpleNamespace:
    raise TransientError("render-failed")


async def successful_render(
    client: object,
    ticket: SimpleNamespace,
    tags: TagList,
    ticket_id: int,
    settings: Settings,
) -> SimpleNamespace:
    return SimpleNamespace(
        pdf_bytes=b"%PDF-1.7\n%%EOF\n",
        snapshot=SimpleNamespace(ticket=ticket),
    )


def stored_pdf_result(tmp_path: Path) -> SimpleNamespace:
    target_path = tmp_path / "archived.pdf"
    return SimpleNamespace(
        target_path=target_path,
        sidecar_path=target_path.with_suffix(".pdf.json"),
        sha256_hex="deadbeef",
        size_bytes=42,
    )


def fake_store_ticket_files(tmp_path: Path):
    def _store_ticket_files(*args: Any, **kwargs: Any) -> SimpleNamespace:
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
    ) -> SimpleNamespace:
        calls["n"] += 1
        if calls["n"] == 1:
            raise TransientError("transient-render-failure")
        return await successful_render(client, ticket, tags, ticket_id, settings)

    return _build_and_render_pdf
