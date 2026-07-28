"""Supplies deterministic process-ticket doubles for lifecycle, retry, and cleanup tests."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from chronikwerk.adapters.zammad.models import TagList
from chronikwerk.app.jobs.ticket_renderer import RenderedTicket
from chronikwerk.config.settings import Settings
from chronikwerk.domain.errors import TransientError
from tests.support.settings_factory import make_settings

TEST_WEBHOOK_SECRET = "test-webhook-secret"


def post_signed_json(
    client: TestClient, path: str, payload: Any, *, secret: str = TEST_WEBHOOK_SECRET
):
    """Post a JSON body with the HMAC headers expected by ingest."""
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
    """Provide a successful no-op ticket-processing coroutine."""
    return None


def capture_process_ticket_calls(monkeypatch: Any) -> list[tuple[Any, Any, Any]]:
    """Patch ingest processing and return every delivery, payload, and settings call."""
    calls: list[tuple[Any, Any, Any]] = []

    async def _capture(delivery_id: Any, payload: Any, settings: Any) -> None:
        calls.append((delivery_id, payload, settings))

    import chronikwerk.app.routes.ingest as ingest_route

    monkeypatch.setattr(ingest_route, "process_ticket", _capture)
    return calls


def install_noop_ingest_processing(monkeypatch: Any) -> None:
    """Disable background ticket work while an endpoint test targets middleware behavior."""
    import chronikwerk.app.routes.ingest as ingest_route

    monkeypatch.setattr(ingest_route, "process_ticket", noop_process_ticket)


def exhaust_signed_rate_limit(client: TestClient, path: str, payload: Any):
    """Consume a two-request burst and return the rate-limited third response."""
    assert post_signed_json(client, path, payload).status_code == 202
    assert post_signed_json(client, path, payload).status_code == 202
    return post_signed_json(client, path, payload)


def process_ticket_settings(storage_root: Path) -> Settings:
    """Build baseline settings for process-ticket tests."""
    return make_settings(str(storage_root))


def process_ticket_payload(ticket_id: int = 321) -> dict[str, dict[str, int]]:
    """Build a representative process-ticket webhook payload."""
    return {"ticket": {"id": ticket_id}}


def fake_ticket(ticket_id: int, title: str) -> SimpleNamespace:
    """Build a representative Zammad ticket response."""
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
    """In-memory logger that records exception event names without emitting output."""

    def __init__(self) -> None:
        self.exception_events: list[str] = []

    def info(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def exception(self, event: str, **_kwargs: Any) -> None:
        self.exception_events.append(event)


class BaseProcessTicketClient:
    """Minimal async Zammad client double with mutable tag state and article recording."""

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
    """Client double used to observe cleanup behavior after processing."""

    title = "cleanup"


class SerializingProcessTicketClient(BaseProcessTicketClient):
    """Client double that exposes serialization points during concurrent processing."""

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
    """Client double that models a retry while another delivery is in flight."""

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
    """Suppress error-note side effects when a scenario isolates earlier pipeline behavior."""
    return None


async def raise_transient_render_failure(*args: Any, **kwargs: Any) -> SimpleNamespace:
    """Force a retryable render failure to exercise recovery policy."""
    raise TransientError("render-failed")


async def successful_render(
    _client: object,
    ticket: SimpleNamespace,
    _tags: TagList,
    _ticket_id: int,
    _settings: Settings,
) -> RenderedTicket:
    """Return a successful deterministic rendering result for downstream assertions."""
    return RenderedTicket(
        pdf_bytes=b"%PDF-1.7\n%%EOF\n",
        snapshot=SimpleNamespace(ticket=ticket),  # type: ignore[arg-type]
        signing_cert_fingerprint=None,
    )


def stored_pdf_result(tmp_path: Path) -> SimpleNamespace:
    """Build persisted-PDF metadata without touching the filesystem."""
    target_path = tmp_path / "archived.pdf"
    return SimpleNamespace(
        target_path=target_path,
        sidecar_path=target_path.with_suffix(".pdf.json"),
        sha256_hex="deadbeef",
        size_bytes=42,
    )


def fake_store_ticket_files(tmp_path: Path):
    """Record file-store calls while avoiding real archive writes."""

    def _store_ticket_files(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        return stored_pdf_result(tmp_path)

    return _store_ticket_files


def flaky_then_successful_render():
    """Fail the first render attempt, then succeed to prove retry progression."""
    calls = {"n": 0}

    async def _build_and_render_pdf(
        client: object,
        ticket: SimpleNamespace,
        tags: TagList,
        ticket_id: int,
        settings: Settings,
    ) -> RenderedTicket:
        calls["n"] += 1
        if calls["n"] == 1:
            raise TransientError("transient-render-failure")
        return await successful_render(client, ticket, tags, ticket_id, settings)

    return _build_and_render_pdf
