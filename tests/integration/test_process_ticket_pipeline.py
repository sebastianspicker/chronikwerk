"""Exercise the current ticket-processing pipeline across Zammad and storage boundaries."""

from __future__ import annotations

import asyncio
import errno
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
import respx

from chronikwerk._version import VERSION
from chronikwerk.adapters.storage.layout import build_filename_from_pattern
from chronikwerk.app.jobs import _ticket_pipeline as ticket_pipeline_module
from chronikwerk.app.jobs import (
    _ticket_pipeline_errors as ticket_pipeline_errors_module,
)
from chronikwerk.app.jobs.process_ticket import process_ticket
from chronikwerk.config.settings import Settings
from chronikwerk.domain.state_machine import (
    DONE_TAG,
    ERROR_TAG,
    PROCESSING_TAG,
    TRIGGER_TAG,
)
from tests.support.settings_factory import make_settings
from tests.support.zammad_client_helpers import called_tag_items


def _test_settings(storage_root: str) -> Settings:
    """Build settings isolated to this test scenario."""
    return make_settings(storage_root)


def _payload(request_id: str, *, force_reprocess: bool = False) -> dict[str, Any]:
    """Return a representative signed webhook payload for this scenario."""
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
    """Pin processing time so archive paths and notes are deterministic."""
    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(ticket_pipeline_module, "now_utc", lambda: fixed_now)
    monkeypatch.setattr(ticket_pipeline_errors_module, "now_utc", lambda: fixed_now)
    return fixed_now


def _ticket_json(archive_path: Any) -> dict[str, Any]:
    """Return a representative Zammad ticket API response."""
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
    """Return a representative Zammad article API response."""
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
    """Build the minimal article set used by one-article pipeline cases."""
    return [_article_json(1)]


def _two_articles() -> list[dict[str, Any]]:
    """Build an ordered article set for multi-article pipeline cases."""
    return [
        _article_json(1),
        _article_json(2, subject="World", body="<p>World Hello</p>"),
    ]


@dataclass
class _ProcessRoutes:
    """Collect routes so each scenario controls upstream responses."""

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
    """Create controllable Zammad routes for ticket-processing scenarios."""
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
    """Return the expected persisted PDF path for the scenario."""
    date_iso = fixed_now.date().isoformat()
    expected_filename = build_filename_from_pattern(
        settings.storage.filename_pattern,
        ticket_number="20240123",
        timestamp_utc=date_iso,
    )
    return tmp_path / "agent" / "A" / "B" / "C" / expected_filename


def _assert_error_tags(
    routes: _ProcessRoutes,
    *,
    trigger_in_added: bool,
    trigger_in_removed: bool = False,
    processing_in_removed: bool = True,
) -> None:
    """Assert the common processing/error tag transition for failed runs."""
    added = called_tag_items(routes.add_tag)
    removed = called_tag_items(routes.remove_tag)

    assert PROCESSING_TAG in added
    assert DONE_TAG not in added
    assert (TRIGGER_TAG in added) is trigger_in_added
    assert ERROR_TAG in added
    if processing_in_removed:
        assert PROCESSING_TAG in removed
    if trigger_in_removed:
        assert TRIGGER_TAG in removed


def _assert_error_note(routes: _ProcessRoutes, *body_parts: str) -> None:
    """Assert that the failure note exists and includes each expected fragment."""
    assert routes.note.called
    req = json.loads(routes.note.calls[0].request.content.decode("utf-8"))
    assert f"PDF archiver error ({VERSION})" in req["subject"]
    for body_part in body_parts:
        assert body_part in req["body"]


def _assert_success_tags(routes: _ProcessRoutes) -> None:
    """Assert the common processing/done tag transition for successful runs."""
    added = called_tag_items(routes.add_tag)
    removed = called_tag_items(routes.remove_tag)

    assert PROCESSING_TAG in added
    assert DONE_TAG in added
    assert ERROR_TAG not in added
    assert TRIGGER_TAG in removed
    assert ERROR_TAG in removed
    assert PROCESSING_TAG in removed


def test_process_ticket_pipeline_happy_path_writes_pdf_and_updates_tags(
    tmp_path, monkeypatch
) -> None:
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
        _assert_success_tags(routes)

        assert routes.note.called
        req = json.loads(routes.note.calls[0].request.content.decode("utf-8"))
        assert req["ticket_id"] == 123
        assert f"PDF archived ({VERSION})" in req["subject"]
        assert str(expected_path.parent) in req["body"]
        assert expected_path.name in req["body"]
        assert str(len(written)) in req["body"]
        assert "req-123" in req["body"]


def test_process_ticket_pipeline_failure_sets_error_tag_and_posts_note(
    tmp_path, monkeypatch
) -> None:
    settings = _test_settings(str(tmp_path))
    fixed_now = _patch_fixed_now(monkeypatch)
    payload = _payload("req-err-1")

    def _boom(*_args, **_kwargs) -> None:
        raise PermissionError("no-write")

    monkeypatch.setattr(ticket_pipeline_module, "store_ticket_files", _boom)

    with respx.mock:
        routes = _register_process_routes(articles=_single_article())

        asyncio.run(process_ticket("delivery-err-1", payload, settings))

        expected_path = _expected_pdf_path(tmp_path, settings, fixed_now)
        assert not expected_path.exists()

        _assert_error_tags(routes, trigger_in_added=False)
        _assert_error_note(routes, "Permanent", "PermissionError")


def test_process_ticket_pipeline_transient_failure_keeps_trigger_and_posts_note(
    tmp_path, monkeypatch
) -> None:
    settings = _test_settings(str(tmp_path))
    _patch_fixed_now(monkeypatch)
    payload = _payload("req-err-transient-1")

    def _boom(*_args, **_kwargs) -> None:
        raise OSError(errno.EAGAIN, "try again")

    monkeypatch.setattr(ticket_pipeline_module, "store_ticket_files", _boom)

    with respx.mock:
        routes = _register_process_routes(articles=_single_article())

        asyncio.run(process_ticket("delivery-err-transient-1", payload, settings))

        _assert_error_tags(routes, trigger_in_added=True)
        _assert_error_note(routes, "Transient")


def test_process_ticket_pipeline_force_reprocess_overrides_done_tag(tmp_path, monkeypatch) -> None:
    settings = _test_settings(str(tmp_path))
    _patch_fixed_now(monkeypatch)
    payload = _payload("req-force-1", force_reprocess=True)

    with respx.mock:
        routes = _register_process_routes(tags=[DONE_TAG])

        asyncio.run(process_ticket("delivery-force-1", payload, settings))

        added = called_tag_items(routes.add_tag)
        removed = called_tag_items(routes.remove_tag)

        assert PROCESSING_TAG in added
        assert DONE_TAG in added
        assert DONE_TAG in removed
        assert routes.note.called


def test_process_ticket_pipeline_invalid_archive_path_is_permanent_and_writes_no_files(
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

        _assert_error_tags(
            routes,
            trigger_in_added=False,
            trigger_in_removed=True,
            processing_in_removed=False,
        )
        _assert_error_note(routes, "Permanent", "ValueError")


def test_process_ticket_pipeline_enforces_pdf_max_articles_setting(tmp_path, monkeypatch) -> None:
    settings = Settings.from_mapping(
        {
            "zammad": {"base_url": "https://zammad.example.local", "api_token": "test-token"},
            "storage": {"root": str(tmp_path)},
            "hardening": {"transport": {"allow_private_networks": True}},
            "pdf": {"max_articles": 1},
        }
    )
    _patch_fixed_now(monkeypatch)
    payload = _payload("req-max-articles-1")

    with respx.mock:
        routes = _register_process_routes(articles=_two_articles())

        asyncio.run(process_ticket("delivery-max-articles-1", payload, settings))

        _assert_error_tags(routes, trigger_in_added=False, trigger_in_removed=True)

        _assert_error_note(routes, "Permanent", "too many articles")


def test_process_ticket_pipeline_pdf_max_articles_zero_disables_limit(
    tmp_path, monkeypatch
) -> None:
    settings = Settings.from_mapping(
        {
            "zammad": {"base_url": "https://zammad.example.local", "api_token": "test-token"},
            "storage": {"root": str(tmp_path)},
            "hardening": {"transport": {"allow_private_networks": True}},
            "pdf": {"max_articles": 0},
        }
    )
    _patch_fixed_now(monkeypatch)
    payload = _payload("req-max-articles-disabled")

    with respx.mock:
        routes = _register_process_routes(articles=_two_articles())

        asyncio.run(process_ticket("delivery-max-articles-disabled", payload, settings))

        _assert_success_tags(routes)

        assert routes.note.called


def test_process_ticket_pipeline_skips_ticket_without_required_trigger(
    tmp_path, monkeypatch
) -> None:
    settings = _test_settings(str(tmp_path))
    _patch_fixed_now(monkeypatch)

    with respx.mock:
        routes = _register_process_routes(tags=["not-the-trigger"], include_articles_route=False)

        result = asyncio.run(process_ticket("delivery-skip-1", _payload("req-skip-1"), settings))

        assert result.status == "skipped_not_triggered"
        assert result.ticket_id == 123
        assert routes.ticket.called
        assert routes.tags.called
        assert not routes.remove_tag.called
        assert not routes.add_tag.called
        assert not routes.note.called
