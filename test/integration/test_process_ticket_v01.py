from __future__ import annotations

import asyncio
import errno
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
import respx

from test.support.settings_factory import make_settings
from zammad_pdf_archiver._version import VERSION
from zammad_pdf_archiver.adapters.storage.layout import build_filename_from_pattern
from zammad_pdf_archiver.app.jobs import process_ticket as process_ticket_module
from zammad_pdf_archiver.app.jobs.process_ticket import process_ticket
from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.domain.state_machine import (
    DONE_TAG,
    ERROR_TAG,
    PROCESSING_TAG,
    TRIGGER_TAG,
)


def _test_settings(storage_root: str) -> Settings:
    return make_settings(storage_root)


def _called_tag_items(route: respx.Route) -> list[str]:
    items: list[str] = []
    for call in route.calls:
        body = json.loads(call.request.content.decode("utf-8"))
        items.append(body.get("item"))
    return items


def _payload(request_id: str, *, force_reprocess: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "_request_id": request_id,
        "user": {"login": "agent-from-webhook"},
    }
    if force_reprocess:
        payload["ticket_id"] = 123
        payload["_force_reprocess"] = True
    else:
        payload["ticket"] = {"id": 123}
    return payload


def _patch_fixed_now(monkeypatch) -> datetime:  # noqa: ANN001
    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(process_ticket_module, "now_utc", lambda: fixed_now)
    return fixed_now


def _ticket_json(archive_path: Any) -> dict[str, Any]:
    return {
        "id": 123,
        "number": "20240123",
        "owner": {"login": "agent"},
        "updated_by": {"login": "fallback-agent"},
        "preferences": {
            "custom_fields": {
                "archive_user_mode": "owner",
                "archive_path": archive_path,
            }
        },
    }


def _article_json(
    article_id: int,
    *,
    subject: str = "Hello",
    body: str = "<p>Hello World</p>",
) -> dict[str, Any]:
    return {
        "id": article_id,
        "created_at": f"2026-02-07T11:59:{(article_id - 1) * 30:02d}Z",
        "internal": False,
        "subject": subject,
        "body": body,
        "content_type": "text/html",
        "from": "customer@example.invalid",
        "attachments": [],
    }


def _single_article() -> list[dict[str, Any]]:
    return [_article_json(1)]


def _two_articles() -> list[dict[str, Any]]:
    return [
        _article_json(1),
        _article_json(2, subject="World", body="<p>World Hello</p>"),
    ]


@dataclass
class _ProcessRoutes:
    ticket: respx.Route
    tags: respx.Route
    articles: respx.Route | None
    remove_tag: respx.Route
    add_tag: respx.Route
    note: respx.Route


def _register_process_routes(
    *,
    archive_path: Any = ("A", "B", "C"),
    tags: list[str] | None = None,
    articles: list[dict[str, Any]] | None = None,
    include_articles_route: bool = True,
) -> _ProcessRoutes:
    ticket_route = respx.get("https://zammad.example.local/api/v1/tickets/123").mock(
        return_value=httpx.Response(200, json=_ticket_json(archive_path))
    )
    tags_route = respx.get(
        "https://zammad.example.local/api/v1/tags",
        params={"object": "Ticket", "o_id": "123"},
    ).mock(return_value=httpx.Response(200, json=tags or [TRIGGER_TAG]))
    articles_route = None
    if include_articles_route:
        articles_route = respx.get(
            "https://zammad.example.local/api/v1/ticket_articles/by_ticket/123"
        ).mock(return_value=httpx.Response(200, json=articles if articles is not None else []))
    remove_tag_route = respx.post("https://zammad.example.local/api/v1/tags/remove").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    add_tag_route = respx.post("https://zammad.example.local/api/v1/tags/add").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    note_route = respx.post("https://zammad.example.local/api/v1/ticket_articles").mock(
        return_value=httpx.Response(
            200,
            json={"id": 999, "internal": True, "subject": "ok", "body": "<p>ok</p>"},
        )
    )
    return _ProcessRoutes(
        ticket=ticket_route,
        tags=tags_route,
        articles=articles_route,
        remove_tag=remove_tag_route,
        add_tag=add_tag_route,
        note=note_route,
    )


def _expected_pdf_path(tmp_path, settings: Settings, fixed_now: datetime):  # noqa: ANN001
    date_iso = fixed_now.date().isoformat()
    expected_filename = build_filename_from_pattern(
        settings.storage.filename_pattern,
        ticket_number="20240123",
        timestamp_utc=date_iso,
    )
    return tmp_path / "agent" / "A" / "B" / "C" / expected_filename


def test_process_ticket_v01_happy_path_writes_pdf_and_updates_tags(tmp_path, monkeypatch) -> None:
    settings = _test_settings(str(tmp_path))
    fixed_now = _patch_fixed_now(monkeypatch)
    payload = _payload("req-123")

    with respx.mock:
        routes = _register_process_routes(
            archive_path="A > B > C",
            articles=_single_article(),
        )

        asyncio.run(process_ticket("delivery-happy-1", payload, settings))

        # Idempotency: same delivery id should be skipped entirely.
        asyncio.run(process_ticket("delivery-happy-1", payload, settings))

        assert routes.ticket.call_count == 1
        assert routes.tags.call_count == 1

        # File written in the expected directory.
        expected_path = _expected_pdf_path(tmp_path, settings, fixed_now)
        assert expected_path.exists()
        written = expected_path.read_bytes()
        assert written.startswith(b"%PDF")
        assert b"archived at" not in written

        assert routes.articles is not None and routes.articles.called
        added = _called_tag_items(routes.add_tag)
        removed = _called_tag_items(routes.remove_tag)

        assert PROCESSING_TAG in added
        assert DONE_TAG in added
        assert ERROR_TAG not in added

        assert TRIGGER_TAG in removed
        assert ERROR_TAG in removed
        assert PROCESSING_TAG in removed

        assert routes.note.called
        req = json.loads(routes.note.calls[0].request.content.decode("utf-8"))
        assert req["ticket_id"] == 123
        assert f"PDF archived ({VERSION})" in req["subject"]
        assert str(expected_path.parent) in req["body"]
        assert expected_path.name in req["body"]
        assert str(len(written)) in req["body"]
        assert "req-123" in req["body"]


def test_process_ticket_v01_failure_sets_error_tag_and_posts_note(tmp_path, monkeypatch) -> None:
    settings = _test_settings(str(tmp_path))
    fixed_now = _patch_fixed_now(monkeypatch)
    payload = _payload("req-err-1")

    def _boom(*_args, **_kwargs) -> None:
        raise PermissionError("no-write")

    monkeypatch.setattr(process_ticket_module, "store_ticket_files", _boom)

    with respx.mock:
        routes = _register_process_routes(articles=_single_article())

        asyncio.run(process_ticket("delivery-err-1", payload, settings))

        expected_path = _expected_pdf_path(tmp_path, settings, fixed_now)
        assert not expected_path.exists()

        added = _called_tag_items(routes.add_tag)
        removed = _called_tag_items(routes.remove_tag)

        assert PROCESSING_TAG in added
        assert DONE_TAG not in added
        assert TRIGGER_TAG not in added  # permanent: drop trigger to prevent loops
        assert ERROR_TAG in added

        assert PROCESSING_TAG in removed  # removed during apply_error/best-effort cleanup

        assert routes.note.called
        req = json.loads(routes.note.calls[0].request.content.decode("utf-8"))
        assert f"PDF archiver error ({VERSION})" in req["subject"]
        assert "Permanent" in req["body"]
        assert "PermissionError" in req["body"]


def test_process_ticket_v01_transient_failure_keeps_trigger_and_posts_note(
    tmp_path, monkeypatch
) -> None:
    settings = _test_settings(str(tmp_path))
    _patch_fixed_now(monkeypatch)
    payload = _payload("req-err-transient-1")

    def _boom(*_args, **_kwargs) -> None:
        raise OSError(errno.EAGAIN, "try again")

    monkeypatch.setattr(process_ticket_module, "store_ticket_files", _boom)

    with respx.mock:
        routes = _register_process_routes(articles=_single_article())

        asyncio.run(process_ticket("delivery-err-transient-1", payload, settings))

        added = _called_tag_items(routes.add_tag)
        removed = _called_tag_items(routes.remove_tag)

        assert PROCESSING_TAG in added
        assert DONE_TAG not in added
        assert TRIGGER_TAG in added  # transient: keep trigger for retries
        assert ERROR_TAG in added

        assert PROCESSING_TAG in removed

        assert routes.note.called
        req = json.loads(routes.note.calls[0].request.content.decode("utf-8"))
        assert f"PDF archiver error ({VERSION})" in req["subject"]
        assert "Transient" in req["body"]


def test_process_ticket_v01_force_reprocess_overrides_done_tag(tmp_path, monkeypatch) -> None:
    settings = _test_settings(str(tmp_path))
    _patch_fixed_now(monkeypatch)
    payload = _payload("req-force-1", force_reprocess=True)

    with respx.mock:
        routes = _register_process_routes(tags=[DONE_TAG])

        asyncio.run(process_ticket("delivery-force-1", payload, settings))

        added = _called_tag_items(routes.add_tag)
        removed = _called_tag_items(routes.remove_tag)

        assert PROCESSING_TAG in added
        assert DONE_TAG in added
        assert DONE_TAG in removed
        assert routes.note.called


def test_process_ticket_v01_invalid_archive_path_is_permanent_and_writes_no_files(
    tmp_path, monkeypatch
) -> None:
    settings = _test_settings(str(tmp_path))
    _patch_fixed_now(monkeypatch)
    payload = _payload("req-path-invalid-1")

    with respx.mock:
        routes = _register_process_routes(
            archive_path=["A", "..", "C"],
            include_articles_route=False,
        )

        asyncio.run(process_ticket("delivery-path-invalid-1", payload, settings))

        assert list(tmp_path.rglob("*.pdf")) == []
        assert list(tmp_path.rglob("*.pdf.json")) == []

        added = _called_tag_items(routes.add_tag)
        removed = _called_tag_items(routes.remove_tag)

        assert PROCESSING_TAG in added
        assert DONE_TAG not in added
        assert TRIGGER_TAG not in added
        assert ERROR_TAG in added

        assert PROCESSING_TAG in removed
        assert TRIGGER_TAG in removed

        assert routes.note.called
        req = json.loads(routes.note.calls[0].request.content.decode("utf-8"))
        assert f"PDF archiver error ({VERSION})" in req["subject"]
        assert "Permanent" in req["body"]
        assert "ValueError" in req["body"]


def test_process_ticket_v01_enforces_pdf_max_articles_setting(tmp_path, monkeypatch) -> None:
    settings = Settings.from_mapping(
        {
            "zammad": {"base_url": "https://zammad.example.local", "api_token": "test-token"},
            "storage": {"root": str(tmp_path)},
            "pdf": {"max_articles": 1},
        }
    )
    _patch_fixed_now(monkeypatch)
    payload = _payload("req-max-articles-1")

    with respx.mock:
        routes = _register_process_routes(articles=_two_articles())

        asyncio.run(process_ticket("delivery-max-articles-1", payload, settings))

        removed = _called_tag_items(routes.remove_tag)
        added = _called_tag_items(routes.add_tag)

        assert TRIGGER_TAG in removed
        assert PROCESSING_TAG in added
        assert ERROR_TAG in added
        assert DONE_TAG not in added

        assert routes.note.called
        req = json.loads(routes.note.calls[0].request.content.decode("utf-8"))
        assert "Permanent" in req["body"]
        assert "too many articles" in req["body"]


def test_process_ticket_v01_pdf_max_articles_zero_disables_limit(tmp_path, monkeypatch) -> None:
    settings = Settings.from_mapping(
        {
            "zammad": {"base_url": "https://zammad.example.local", "api_token": "test-token"},
            "storage": {"root": str(tmp_path)},
            "pdf": {"max_articles": 0},
        }
    )
    _patch_fixed_now(monkeypatch)
    payload = _payload("req-max-articles-disabled")

    with respx.mock:
        routes = _register_process_routes(articles=_two_articles())

        asyncio.run(process_ticket("delivery-max-articles-disabled", payload, settings))

        removed = _called_tag_items(routes.remove_tag)
        added = _called_tag_items(routes.add_tag)

        assert TRIGGER_TAG in removed
        assert PROCESSING_TAG in added
        assert DONE_TAG in added
        assert ERROR_TAG not in added

        assert routes.note.called
