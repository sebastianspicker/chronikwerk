from __future__ import annotations

import asyncio
import errno
import json
from datetime import UTC, datetime

import httpx
import pytest
import respx

from test.integration.test_process_ticket_v01 import (
    _article_json,
    _article_with_attachment_json,
    _assert_attachment_fetch_failure,
    _assert_error_note_basics,
    _assert_error_tag_transitions,
    _assert_field_failure_note,
    _assert_max_article_failure,
    _assert_permanent_drop_trigger_tags,
    _assert_permanent_field_failure_tags,
    _assert_permanent_result_no_files,
    _assert_success_tags_and_note_posted,
    _called_tag_items,
    _mock_error_side_effect_routes,
    _mock_standard_ticket_reads,
    _mock_ticket_and_tags,
    _mock_ticket_reads_with_tags,
    _posted_article,
    _second_article_json,
    _settings_with_pdf,
    _test_settings,
)
from test.support.checks import check
from test.support.credentials import fake_credential
from test.support.time_control import freeze_process_ticket_now
from zammad_pdf_archiver._version import VERSION
from zammad_pdf_archiver.app.jobs import process_ticket as process_ticket_module
from zammad_pdf_archiver.app.jobs.process_ticket import process_ticket
from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.domain.state_machine import (
    DONE_TAG,
    ERROR_TAG,
    PROCESSING_TAG,
    TRIGGER_TAG,
)


def test_process_ticket_v01_failure_sets_error_tag_and_posts_note(tmp_path, monkeypatch) -> None:
    settings = _test_settings(str(tmp_path))
    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    freeze_process_ticket_now(monkeypatch, process_ticket_module, fixed_now)
    payload = {
        "ticket": {"id": 123},
        "_request_id": "req-err-1",
        "user": {"login": "agent-from-webhook"},
    }

    def _boom(*_args, **_kwargs) -> None:
        raise PermissionError("no-write token=super-secret")

    monkeypatch.setattr(process_ticket_module, "store_ticket_files", _boom)

    with respx.mock:
        _mock_standard_ticket_reads()
        remove_tag_route, add_tag_route, article_route = _mock_error_side_effect_routes()

        result = asyncio.run(process_ticket("delivery-err-1", payload, settings))

        _assert_error_tag_transitions(
            add_tag_route=add_tag_route,
            remove_tag_route=remove_tag_route,
            transient=False,
        )

        check(not not article_route.called, "assertion failed")
        req = _posted_article(article_route)
        body = _assert_error_note_basics(
            req,
            classification="Permanent",
            request_id="req-err-1",
            delivery_id="delivery-err-1",
        )
        check(not not result.status == "failed_permanent", "assertion failed")
        check(not not result.classification == "Permanent", "assertion failed")
        check(not result.error_note_posted is not True, "assertion failed")
        check(not result.error_tag_applied is not True, "assertion failed")
        check(not "PermissionError" not in body, "assertion failed")
        check(not "Storage permission denied" not in body, "assertion failed")
        check(not not "super-secret" not in body, "assertion failed")
        check(not "token=[redacted]" not in body, "assertion failed")


def test_process_ticket_v01_transient_failure_keeps_trigger_and_posts_note(
    tmp_path, monkeypatch
) -> None:
    settings = _test_settings(str(tmp_path))
    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    freeze_process_ticket_now(monkeypatch, process_ticket_module, fixed_now)
    payload = {
        "ticket": {"id": 123},
        "_request_id": "req-err-transient-1",
        "user": {"login": "agent-from-webhook"},
    }

    def _boom(*_args, **_kwargs) -> None:
        raise OSError(errno.EAGAIN, "try again")

    monkeypatch.setattr(process_ticket_module, "store_ticket_files", _boom)

    with respx.mock:
        _mock_standard_ticket_reads()
        remove_tag_route, add_tag_route, article_route = _mock_error_side_effect_routes()

        result = asyncio.run(process_ticket("delivery-err-transient-1", payload, settings))

        _assert_error_tag_transitions(
            add_tag_route=add_tag_route,
            remove_tag_route=remove_tag_route,
            transient=True,
        )
        check(not not article_route.called, "assertion failed")
        body = _assert_error_note_basics(
            _posted_article(article_route),
            classification="Transient",
            request_id="req-err-transient-1",
            delivery_id="delivery-err-transient-1",
        )
        check(not not result.status == "failed_transient", "assertion failed")
        check(not not result.classification == "Transient", "assertion failed")
        check(not result.error_note_posted is not True, "assertion failed")
        check(not result.error_tag_applied is not True, "assertion failed")
        check(not "try again" not in body, "assertion failed")
        check(not "Transient failure" not in body, "assertion failed")
        check(not "pdf:sign" not in body, "assertion failed")


@pytest.mark.parametrize(
    (
        "case_id",
        "custom_fields",
        "path_policy",
        "expected_code",
        "expected_fragments",
    ),
    [
        (
            "missing-archive-path",
            {"archive_user_mode": "owner"},
            {},
            "missing_archive_path",
            ["Set custom_fields.archive_path", "Fix ticket fields / path policy validation"],
        ),
        (
            "allow-prefix-rejection",
            {"archive_user_mode": "owner", "archive_path": ["Denied", "Team"]},
            {"allow_prefixes": ["Allowed"]},
            "path_not_allowed",
            ["Check allow_prefixes", "archive_path must match a prefix"],
        ),
        (
            "invalid-filename-pattern",
            {"archive_user_mode": "owner", "archive_path": ["Allowed"]},
            {"filename_pattern": "{ticket_number}/bad.pdf"},
            "invalid_filename",
            ["Check filename_pattern", "path policy"],
        ),
    ],
)
def test_process_ticket_v01_field_failures_post_actionable_error_notes(
    tmp_path,
    monkeypatch,
    case_id: str,
    custom_fields: dict[str, object],
    path_policy: dict[str, object],
    expected_code: str,
    expected_fragments: list[str],
) -> None:
    storage_settings: dict[str, object] = {"root": str(tmp_path)}
    if path_policy:
        storage_settings["path_policy"] = path_policy
    settings = Settings.from_mapping(
        {
            "zammad": {
                "base_url": "https://zammad.example.local",
                "api_token": fake_credential("test-token"),
            },
            "storage": storage_settings,
        }
    )
    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    freeze_process_ticket_now(monkeypatch, process_ticket_module, fixed_now)
    payload = {
        "ticket": {"id": 123},
        "_request_id": f"req-{case_id}",
        "user": {"login": "agent-from-webhook"},
    }

    with respx.mock:
        _mock_ticket_and_tags(custom_fields)
        remove_tag_route, add_tag_route, article_route = _mock_error_side_effect_routes()

        result = asyncio.run(process_ticket(f"delivery-{case_id}", payload, settings))

        _assert_permanent_result_no_files(result, tmp_path)
        _assert_permanent_field_failure_tags(
            add_tag_route=add_tag_route,
            remove_tag_route=remove_tag_route,
        )
        _assert_field_failure_note(
            article_route=article_route,
            case_id=case_id,
            expected_code=expected_code,
            expected_fragments=expected_fragments,
        )


@pytest.mark.parametrize(
    ("status_code", "expected_exception", "expected_action"),
    [
        (401, "AuthError", "Fix Zammad API token/permissions"),
        (404, "NotFoundError", "Ticket/resource not found"),
    ],
)
def test_process_ticket_v01_zammad_permanent_fetch_failures_post_operator_notes(
    tmp_path,
    monkeypatch,
    status_code: int,
    expected_exception: str,
    expected_action: str,
) -> None:
    settings = _test_settings(str(tmp_path))
    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    freeze_process_ticket_now(monkeypatch, process_ticket_module, fixed_now)
    payload = {
        "ticket": {"id": 123},
        "_request_id": f"req-zammad-{status_code}",
        "user": {"login": "agent-from-webhook"},
    }

    with respx.mock:
        respx.get("https://zammad.example.local/api/v1/tickets/123").mock(
            return_value=httpx.Response(status_code, json={"error": "zammad failure"})
        )
        remove_tag_route, add_tag_route, article_route = _mock_error_side_effect_routes()

        result = asyncio.run(process_ticket(f"delivery-zammad-{status_code}", payload, settings))

        check(not not result.status == "failed_permanent", "assertion failed")
        check(not not result.classification == "Permanent", "assertion failed")
        check(not result.error_note_posted is not True, "assertion failed")
        check(not result.error_tag_applied is not True, "assertion failed")
        check(not not list(tmp_path.rglob("*.pdf")) == [], "assertion failed")
        check(not not list(tmp_path.rglob("*.pdf.json")) == [], "assertion failed")

        added = _called_tag_items(add_tag_route)
        removed = _called_tag_items(remove_tag_route)

        check(not not PROCESSING_TAG not in added, "assertion failed")
        check(not ERROR_TAG not in added, "assertion failed")
        check(not not DONE_TAG not in added, "assertion failed")
        check(not PROCESSING_TAG not in removed, "assertion failed")
        check(not TRIGGER_TAG not in removed, "assertion failed")

        body = _assert_error_note_basics(
            _posted_article(article_route),
            classification="Permanent",
            request_id=f"req-zammad-{status_code}",
            delivery_id=f"delivery-zammad-{status_code}",
        )
        check(not expected_exception not in body, "assertion failed")
        check(not expected_action not in body, "assertion failed")
        check(not "permanent_error" not in body, "assertion failed")


def test_process_ticket_v01_zammad_server_failure_posts_transient_note_and_keeps_trigger(
    tmp_path, monkeypatch
) -> None:
    settings = _test_settings(str(tmp_path))
    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    freeze_process_ticket_now(monkeypatch, process_ticket_module, fixed_now)
    payload = {
        "ticket": {"id": 123},
        "_request_id": "req-zammad-500",
        "user": {"login": "agent-from-webhook"},
    }

    with respx.mock:
        ticket_route = respx.get("https://zammad.example.local/api/v1/tickets/123").mock(
            return_value=httpx.Response(500, json={"error": "server failure"})
        )
        remove_tag_route, add_tag_route, article_route = _mock_error_side_effect_routes()

        result = asyncio.run(process_ticket("delivery-zammad-500", payload, settings))

        check(not not ticket_route.call_count == 4, "assertion failed")
        check(not not result.status == "failed_transient", "assertion failed")
        check(not not result.classification == "Transient", "assertion failed")
        check(not result.error_note_posted is not True, "assertion failed")
        check(not result.error_tag_applied is not True, "assertion failed")
        check(not not list(tmp_path.rglob("*.pdf")) == [], "assertion failed")
        check(not not list(tmp_path.rglob("*.pdf.json")) == [], "assertion failed")

        added = _called_tag_items(add_tag_route)
        removed = _called_tag_items(remove_tag_route)

        check(not not PROCESSING_TAG not in added, "assertion failed")
        check(not TRIGGER_TAG not in added, "assertion failed")
        check(not ERROR_TAG not in added, "assertion failed")
        check(not not DONE_TAG not in added, "assertion failed")
        check(not PROCESSING_TAG not in removed, "assertion failed")

        body = _assert_error_note_basics(
            _posted_article(article_route),
            classification="Transient",
            request_id="req-zammad-500",
            delivery_id="delivery-zammad-500",
        )
        check(not "ServerError" not in body, "assertion failed")
        check(not "Transient failure" not in body, "assertion failed")
        check(not "pdf:sign" not in body, "assertion failed")


def test_process_ticket_v01_force_reprocess_overrides_done_tag(tmp_path, monkeypatch) -> None:
    settings = _test_settings(str(tmp_path))
    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    freeze_process_ticket_now(monkeypatch, process_ticket_module, fixed_now)
    payload = {
        "ticket_id": 123,
        "_request_id": "req-force-1",
        "_force_reprocess": True,
        "user": {"login": "agent-from-webhook"},
    }

    with respx.mock:
        _mock_ticket_reads_with_tags(tags=[DONE_TAG])
        remove_tag_route, add_tag_route, article_route = _mock_error_side_effect_routes()

        asyncio.run(process_ticket("delivery-force-1", payload, settings))

        added = _called_tag_items(add_tag_route)
        removed = _called_tag_items(remove_tag_route)

        check(not PROCESSING_TAG not in added, "assertion failed")
        check(not DONE_TAG not in added, "assertion failed")
        check(not DONE_TAG not in removed, "assertion failed")
        check(not not article_route.called, "assertion failed")


def test_process_ticket_v01_invalid_archive_path_is_permanent_and_writes_no_files(
    tmp_path, monkeypatch
) -> None:
    settings = _test_settings(str(tmp_path))
    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    freeze_process_ticket_now(monkeypatch, process_ticket_module, fixed_now)
    payload = {
        "ticket": {"id": 123},
        "_request_id": "req-path-invalid-1",
        "user": {"login": "agent-from-webhook"},
    }

    with respx.mock:
        _mock_ticket_and_tags({"archive_user_mode": "owner", "archive_path": ["A", "..", "C"]})
        remove_tag_route, add_tag_route, article_route = _mock_error_side_effect_routes()

        asyncio.run(process_ticket("delivery-path-invalid-1", payload, settings))

        check(not not list(tmp_path.rglob("*.pdf")) == [], "assertion failed")
        check(not not list(tmp_path.rglob("*.pdf.json")) == [], "assertion failed")

        _assert_permanent_drop_trigger_tags(
            add_tag_route=add_tag_route,
            remove_tag_route=remove_tag_route,
        )

        check(not not article_route.called, "assertion failed")
        req = json.loads(article_route.calls[0].request.content.decode("utf-8"))
        check(not f"PDF archiver error ({VERSION})" not in req["subject"], "assertion failed")
        check(not "Permanent" not in req["body"], "assertion failed")
        check(not "ValueError" not in req["body"], "assertion failed")


def test_process_ticket_v01_enforces_pdf_max_articles_setting(tmp_path, monkeypatch) -> None:
    settings = _settings_with_pdf(tmp_path, {"max_articles": 1})
    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    freeze_process_ticket_now(monkeypatch, process_ticket_module, fixed_now)
    payload = {
        "ticket": {"id": 123},
        "_request_id": "req-max-articles-1",
        "user": {"login": "agent-from-webhook"},
    }

    with respx.mock:
        _mock_standard_ticket_reads(
            articles=[_article_json(), _second_article_json()],
        )
        remove_tag_route, add_tag_route, article_route = _mock_error_side_effect_routes()

        asyncio.run(process_ticket("delivery-max-articles-1", payload, settings))

        _assert_max_article_failure(
            add_tag_route=add_tag_route,
            remove_tag_route=remove_tag_route,
            article_route=article_route,
        )


def test_process_ticket_v01_pdf_max_articles_zero_disables_limit(tmp_path, monkeypatch) -> None:
    settings = _settings_with_pdf(tmp_path, {"max_articles": 0})
    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    freeze_process_ticket_now(monkeypatch, process_ticket_module, fixed_now)
    payload = {
        "ticket": {"id": 123},
        "_request_id": "req-max-articles-disabled",
        "user": {"login": "agent-from-webhook"},
    }

    with respx.mock:
        _mock_standard_ticket_reads(
            articles=[_article_json(), _second_article_json()],
        )
        remove_tag_route, add_tag_route, article_route = _mock_error_side_effect_routes()

        asyncio.run(process_ticket("delivery-max-articles-disabled", payload, settings))

        _assert_success_tags_and_note_posted(
            add_tag_route=add_tag_route,
            remove_tag_route=remove_tag_route,
            article_route=article_route,
        )


def test_process_ticket_v01_attachment_fetch_failure_fails_job(tmp_path, monkeypatch) -> None:
    settings = _settings_with_pdf(
        tmp_path,
        {
            "include_attachment_binary": True,
            "max_attachment_bytes_per_file": 100,
            "max_total_attachment_bytes": 1000,
        },
    )
    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    freeze_process_ticket_now(monkeypatch, process_ticket_module, fixed_now)
    payload = {
        "ticket": {"id": 123},
        "_request_id": "req-attachment-fail",
        "user": {"login": "agent-from-webhook"},
    }

    with respx.mock:
        _mock_standard_ticket_reads(articles=[_article_with_attachment_json()])
        respx.get("https://zammad.example.local/api/v1/ticket_attachment/123/1/10").mock(
            return_value=httpx.Response(503, json={"error": "unavailable"})
        )
        remove_tag_route, add_tag_route, article_route = _mock_error_side_effect_routes()

        result = asyncio.run(process_ticket("delivery-attachment-fail-1", payload, settings))

        _assert_attachment_fetch_failure(
            result=result,
            tmp_path=tmp_path,
            add_tag_route=add_tag_route,
            remove_tag_route=remove_tag_route,
            article_route=article_route,
        )
