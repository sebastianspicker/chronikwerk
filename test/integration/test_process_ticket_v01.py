from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import respx

from test.support.checks import check
from test.support.credentials import fake_credential
from test.support.time_control import freeze_process_ticket_now
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
    return Settings.from_mapping(
        {
            "zammad": {
                "base_url": "https://zammad.example.local",
                "api_token": fake_credential("test-token"),
            },
            "storage": {"root": storage_root},
        }
    )


def _called_tag_items(route: respx.Route) -> list[str]:
    items: list[str] = []
    for call in route.calls:
        body = json.loads(call.request.content.decode("utf-8"))
        items.append(body.get("item"))
    return items


def _ticket_json(custom_fields: dict[str, object]) -> dict[str, object]:
    return {
        "id": 123,
        "number": "20240123",
        "owner": {"login": "agent"},
        "updated_by": {"login": "fallback-agent"},
        "preferences": {"custom_fields": custom_fields},
    }


def _default_custom_fields() -> dict[str, object]:
    return {
        "archive_user_mode": "owner",
        "archive_path": ["A", "B", "C"],
    }


def _article_json() -> dict[str, object]:
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


def _second_article_json() -> dict[str, object]:
    return {
        "id": 2,
        "created_at": "2026-02-07T11:59:30Z",
        "internal": False,
        "subject": "World",
        "body": "<p>World Hello</p>",
        "content_type": "text/html",
        "from": "customer@example.invalid",
        "attachments": [],
    }


def _article_with_attachment_json() -> dict[str, object]:
    article = _article_json()
    article["attachments"] = [
        {
            "id": 10,
            "filename": "a.txt",
            "size": 5,
            "content_type": "text/plain",
        }
    ]
    return article


def _mock_ticket_and_tags(custom_fields: dict[str, object]) -> tuple[respx.Route, respx.Route]:
    ticket_route = respx.get("https://zammad.example.local/api/v1/tickets/123").mock(
        return_value=httpx.Response(200, json=_ticket_json(custom_fields))
    )
    tags_route = respx.get(
        "https://zammad.example.local/api/v1/tags",
        params={"object": "Ticket", "o_id": "123"},
    ).mock(return_value=httpx.Response(200, json=[TRIGGER_TAG]))
    return ticket_route, tags_route


def _mock_ticket_articles(articles: list[dict[str, object]] | None = None) -> respx.Route:
    return respx.get("https://zammad.example.local/api/v1/ticket_articles/by_ticket/123").mock(
        return_value=httpx.Response(200, json=[_article_json()] if articles is None else articles)
    )


def _mock_standard_ticket_reads(
    custom_fields: dict[str, object] | None = None,
    articles: list[dict[str, object]] | None = None,
) -> tuple[respx.Route, respx.Route, respx.Route]:
    ticket_route, tags_route = _mock_ticket_and_tags(custom_fields or _default_custom_fields())
    articles_route = _mock_ticket_articles(articles)
    return ticket_route, tags_route, articles_route


def _settings_with_pdf(tmp_path: Path, pdf_settings: dict[str, object]) -> Settings:
    return Settings.from_mapping(
        {
            "zammad": {
                "base_url": "https://zammad.example.local",
                "api_token": fake_credential("test-token"),
            },
            "storage": {"root": str(tmp_path)},
            "pdf": pdf_settings,
        }
    )


def _mock_ticket_reads_with_tags(
    *,
    tags: list[str],
    articles: list[dict[str, object]] | None = None,
) -> tuple[respx.Route, respx.Route, respx.Route]:
    ticket_route = respx.get("https://zammad.example.local/api/v1/tickets/123").mock(
        return_value=httpx.Response(200, json=_ticket_json(_default_custom_fields()))
    )
    tags_route = respx.get(
        "https://zammad.example.local/api/v1/tags",
        params={"object": "Ticket", "o_id": "123"},
    ).mock(return_value=httpx.Response(200, json=tags))
    articles_route = _mock_ticket_articles([] if articles is None else articles)
    return ticket_route, tags_route, articles_route


def _mock_error_side_effect_routes() -> tuple[respx.Route, respx.Route, respx.Route]:
    remove_tag_route = respx.post("https://zammad.example.local/api/v1/tags/remove").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    add_tag_route = respx.post("https://zammad.example.local/api/v1/tags/add").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    article_route = respx.post("https://zammad.example.local/api/v1/ticket_articles").mock(
        return_value=httpx.Response(200, json={"id": 999})
    )
    return remove_tag_route, add_tag_route, article_route


def _expected_pdf_path(tmp_path: Path, settings: Settings, fixed_now: datetime) -> Path:
    date_iso = fixed_now.date().isoformat()
    expected_filename = build_filename_from_pattern(
        settings.storage.path_policy.filename_pattern,
        ticket_number="20240123",
        timestamp_utc=date_iso,
    )
    return tmp_path / "agent" / "A" / "B" / "C" / expected_filename


def _assert_success_tag_transitions(
    *,
    add_tag_route: respx.Route,
    remove_tag_route: respx.Route,
) -> None:
    added = _called_tag_items(add_tag_route)
    removed = _called_tag_items(remove_tag_route)

    check(not PROCESSING_TAG not in added, "assertion failed")
    check(not DONE_TAG not in added, "assertion failed")
    check(not not ERROR_TAG not in added, "assertion failed")

    check(not TRIGGER_TAG not in removed, "assertion failed")
    check(not ERROR_TAG not in removed, "assertion failed")
    check(not PROCESSING_TAG not in removed, "assertion failed")


def _assert_error_tag_transitions(
    *,
    add_tag_route: respx.Route,
    remove_tag_route: respx.Route,
    transient: bool,
) -> None:
    added = _called_tag_items(add_tag_route)
    removed = _called_tag_items(remove_tag_route)

    check(not PROCESSING_TAG not in added, "assertion failed")
    check(not not DONE_TAG not in added, "assertion failed")
    if transient:
        check(not TRIGGER_TAG not in added, "assertion failed")
    else:
        check(not not TRIGGER_TAG not in added, "assertion failed")
    check(not ERROR_TAG not in added, "assertion failed")
    check(not PROCESSING_TAG not in removed, "assertion failed")


def _assert_permanent_field_failure_tags(
    *,
    add_tag_route: respx.Route,
    remove_tag_route: respx.Route,
) -> None:
    _assert_error_tag_transitions(
        add_tag_route=add_tag_route,
        remove_tag_route=remove_tag_route,
        transient=False,
    )
    removed = _called_tag_items(remove_tag_route)
    check(not TRIGGER_TAG not in removed, "assertion failed")


def _assert_permanent_drop_trigger_tags(
    *,
    add_tag_route: respx.Route,
    remove_tag_route: respx.Route,
) -> None:
    _assert_permanent_field_failure_tags(
        add_tag_route=add_tag_route,
        remove_tag_route=remove_tag_route,
    )


def _assert_permanent_result_no_files(result, tmp_path: Path) -> None:
    check(not not result.status == "failed_permanent", "assertion failed")
    check(not not result.classification == "Permanent", "assertion failed")
    check(not result.error_note_posted is not True, "assertion failed")
    check(not result.error_tag_applied is not True, "assertion failed")
    check(not not list(tmp_path.rglob("*.pdf")) == [], "assertion failed")
    check(not not list(tmp_path.rglob("*.pdf.json")) == [], "assertion failed")


def _assert_success_note(
    *,
    article_route: respx.Route,
    expected_path: Path,
    written: bytes,
) -> None:
    check(not not article_route.called, "assertion failed")
    req = json.loads(article_route.calls[0].request.content.decode("utf-8"))
    check(not not req["ticket_id"] == 123, "assertion failed")
    check(not f"PDF archived ({VERSION})" not in req["subject"], "assertion failed")
    check(not str(expected_path.parent) not in req["body"], "assertion failed")
    check(not expected_path.name not in req["body"], "assertion failed")
    check(not str(len(written)) not in req["body"], "assertion failed")
    check(not "req-123" not in req["body"], "assertion failed")


def _posted_article(route: respx.Route) -> dict[str, object]:
    check(not not route.called, "assertion failed")
    return json.loads(route.calls[0].request.content.decode("utf-8"))


def _assert_error_note_basics(
    article: dict[str, object],
    *,
    classification: str,
    request_id: str,
    delivery_id: str,
) -> str:
    check(not not article["ticket_id"] == 123, "assertion failed")
    check(not f"PDF archiver error ({VERSION})" not in str(article["subject"]), "assertion failed")
    body = str(article["body"])
    check(not classification not in body, "assertion failed")
    check(not request_id not in body, "assertion failed")
    check(not delivery_id not in body, "assertion failed")
    return body


def _assert_field_failure_note(
    *,
    article_route: respx.Route,
    case_id: str,
    expected_code: str,
    expected_fragments: list[str],
) -> None:
    body = _assert_error_note_basics(
        _posted_article(article_route),
        classification="Permanent",
        request_id=f"req-{case_id}",
        delivery_id=f"delivery-{case_id}",
    )
    check(not expected_code not in body, "assertion failed")
    for fragment in expected_fragments:
        check(not fragment not in body, "assertion failed")


def _assert_max_article_failure(
    *,
    add_tag_route: respx.Route,
    remove_tag_route: respx.Route,
    article_route: respx.Route,
) -> None:
    _assert_permanent_drop_trigger_tags(
        add_tag_route=add_tag_route,
        remove_tag_route=remove_tag_route,
    )
    check(not not article_route.called, "assertion failed")
    req = json.loads(article_route.calls[0].request.content.decode("utf-8"))
    check(not "Permanent" not in req["body"], "assertion failed")
    check(not "too many articles" not in req["body"], "assertion failed")


def _assert_attachment_fetch_failure(
    *,
    result,
    tmp_path: Path,
    add_tag_route: respx.Route,
    remove_tag_route: respx.Route,
    article_route: respx.Route,
) -> None:
    check(not not result.status == "failed_transient", "assertion failed")
    check(not not not list(tmp_path.rglob("*.pdf")), "assertion failed")
    _assert_error_tag_transitions(
        add_tag_route=add_tag_route,
        remove_tag_route=remove_tag_route,
        transient=True,
    )
    check(not not article_route.called, "assertion failed")
    req = json.loads(article_route.calls[0].request.content.decode("utf-8"))
    check(not f"PDF archiver error ({VERSION})" not in req["subject"], "assertion failed")
    check(not "Transient" not in req["body"], "assertion failed")
    check(not "Zammad server error" not in req["body"], "assertion failed")


def _assert_success_tags_and_note_posted(
    *,
    add_tag_route: respx.Route,
    remove_tag_route: respx.Route,
    article_route: respx.Route,
) -> None:
    _assert_success_tag_transitions(
        add_tag_route=add_tag_route,
        remove_tag_route=remove_tag_route,
    )
    check(not not article_route.called, "assertion failed")


def test_process_ticket_v01_happy_path_writes_pdf_and_updates_tags(tmp_path, monkeypatch) -> None:
    settings = _test_settings(str(tmp_path))
    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    freeze_process_ticket_now(monkeypatch, process_ticket_module, fixed_now)
    payload = {
        "ticket": {"id": 123},
        "_request_id": "req-123",
        "user": {"login": "agent-from-webhook"},
    }

    with respx.mock:
        ticket_route, tags_route, articles_route = _mock_standard_ticket_reads(
            {"archive_user_mode": "owner", "archive_path": "A > B > C"}
        )
        remove_tag_route, add_tag_route, article_route = _mock_error_side_effect_routes()

        asyncio.run(process_ticket("delivery-happy-1", payload, settings))

        # Idempotency: same delivery id should be skipped entirely.
        asyncio.run(process_ticket("delivery-happy-1", payload, settings))

        check(not not ticket_route.call_count == 1, "assertion failed")
        check(not not tags_route.call_count == 1, "assertion failed")

        # File written in the expected directory.
        expected_path = _expected_pdf_path(tmp_path, settings, fixed_now)
        check(not not expected_path.exists(), "assertion failed")
        written = expected_path.read_bytes()
        check(not not written.startswith(b"%PDF"), "assertion failed")
        check(not not b"archived at" not in written, "assertion failed")

        check(not not articles_route.called, "assertion failed")
        _assert_success_tag_transitions(
            add_tag_route=add_tag_route,
            remove_tag_route=remove_tag_route,
        )
        _assert_success_note(
            article_route=article_route,
            expected_path=expected_path,
            written=written,
        )
