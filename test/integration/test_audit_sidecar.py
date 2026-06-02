from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime

import httpx
import respx

from test.support.checks import check
from test.support.credentials import fake_credential
from test.support.time_control import freeze_process_ticket_now
from zammad_pdf_archiver.adapters.storage.layout import build_filename_from_pattern
from zammad_pdf_archiver.app.jobs import process_ticket as process_ticket_module
from zammad_pdf_archiver.app.jobs.process_ticket import process_ticket
from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.domain.audit import AuditRecordInput, build_audit_record


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


def _audit_ticket_payload() -> dict[str, object]:
    return {
        "id": 123,
        "number": "20240123",
        "title": "Example Ticket",
        "owner": {"login": "agent"},
        "updated_by": {"login": "fallback-agent"},
        "preferences": {
            "custom_fields": {
                "archive_user_mode": "owner",
                "archive_path": "A > B > C",
            }
        },
    }


def _audit_article_payload() -> list[dict[str, object]]:
    return [
        {
            "id": 1,
            "created_at": "2026-02-07T11:59:00Z",
            "internal": False,
            "subject": "Hello",
            "body": "<p>Hello World</p>",
            "content_type": "text/html",
            "from": "customer@example.invalid",
            "attachments": [
                {
                    "id": 10,
                    "filename": "a.txt",
                    "size": 5,
                    "content_type": "text/plain",
                }
            ],
        }
    ]


def _mock_audit_sidecar_reads() -> None:
    respx.get("https://zammad.example.local/api/v1/tickets/123").mock(
        return_value=httpx.Response(200, json=_audit_ticket_payload())
    )
    respx.get(
        "https://zammad.example.local/api/v1/tags",
        params={"object": "Ticket", "o_id": "123"},
    ).mock(return_value=httpx.Response(200, json=["pdf:sign"]))
    respx.get("https://zammad.example.local/api/v1/ticket_articles/by_ticket/123").mock(
        return_value=httpx.Response(200, json=_audit_article_payload())
    )


def _mock_audit_sidecar_zammad_routes() -> respx.Route:
    _mock_audit_sidecar_reads()
    respx.post("https://zammad.example.local/api/v1/tags/remove").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    respx.post("https://zammad.example.local/api/v1/tags/add").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    return respx.post("https://zammad.example.local/api/v1/ticket_articles").mock(
        return_value=httpx.Response(
            200,
            json={"id": 999, "internal": True, "subject": "ok", "body": "<p>ok</p>"},
        )
    )


def _expected_audit_paths(
    *,
    tmp_path,
    settings: Settings,
    fixed_now: datetime,
) -> tuple[object, object]:
    date_iso = fixed_now.date().isoformat()
    expected_filename = build_filename_from_pattern(
        settings.storage.path_policy.filename_pattern,
        ticket_number="20240123",
        timestamp_utc=date_iso,
    )
    expected_pdf_path = tmp_path / "agent" / "A" / "B" / "C" / expected_filename
    expected_sidecar_path = expected_pdf_path.parent / (expected_pdf_path.name + ".json")
    return expected_pdf_path, expected_sidecar_path


def _assert_audit_sidecar(
    *,
    expected_pdf_path,
    expected_sidecar_path,
) -> str:
    check(not not expected_pdf_path.exists(), "assertion failed")
    check(not not expected_sidecar_path.exists(), "assertion failed")

    pdf_bytes = expected_pdf_path.read_bytes()
    sha256_hex = hashlib.sha256(pdf_bytes).hexdigest()

    audit = json.loads(expected_sidecar_path.read_text("utf-8"))
    check(not not audit["sha256"] == sha256_hex, "assertion failed")
    check(not not audit["storage_path"] == str(expected_pdf_path), "assertion failed")
    check(not not audit["ticket_id"] == 123, "assertion failed")
    check(not not audit["ticket_number"] == "20240123", "assertion failed")
    check(
        not not audit["attachment_summary"]
        == {
            "total": 1,
            "written": 0,
            "metadata_only": 1,
            "skipped": 1,
            "skipped_reasons": {"binary_inclusion_disabled": 1},
        },
        "assertion failed",
    )
    check(not not "attachments" not in audit, "assertion failed")
    return sha256_hex


def test_audit_sidecar_written_next_to_pdf_and_matches_sha256(tmp_path, monkeypatch) -> None:
    settings = _test_settings(str(tmp_path))
    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    freeze_process_ticket_now(monkeypatch, process_ticket_module, fixed_now)

    payload = {
        "ticket": {"id": 123},
        "_request_id": "req-audit-1",
        "user": {"login": "agent-from-webhook"},
    }

    with respx.mock:
        article_route = _mock_audit_sidecar_zammad_routes()

        asyncio.run(process_ticket("delivery-audit-1", payload, settings))

        expected_pdf_path, expected_sidecar_path = _expected_audit_paths(
            tmp_path=tmp_path,
            settings=settings,
            fixed_now=fixed_now,
        )
        sha256_hex = _assert_audit_sidecar(
            expected_pdf_path=expected_pdf_path,
            expected_sidecar_path=expected_sidecar_path,
        )

        check(not not article_route.called, "assertion failed")
        posted = json.loads(article_route.calls[0].request.content.decode("utf-8"))
        check(not sha256_hex not in posted["body"], "assertion failed")
        check(not str(expected_sidecar_path) not in posted["body"], "assertion failed")


def test_audit_sidecar_schema_core_fields_are_json_serializable() -> None:
    record = build_audit_record(
        AuditRecordInput(
            ticket_id=123,
            ticket_number="20240123",
            title="Example Ticket",
            created_at=datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC),
            storage_path="/archive/Ticket-20240123.pdf",
            sha256="a" * 64,
        )
    )

    encoded = json.dumps(record, sort_keys=True)
    decoded = json.loads(encoded)

    check(not not decoded["ticket_id"] == 123, "assertion failed")
    check(not not decoded["ticket_number"] == "20240123", "assertion failed")
    check(not not decoded["sha256"] == "a" * 64, "assertion failed")
    check(not not decoded["signing"] == {"enabled": False, "tsa_used": False}, "assertion failed")
    check(not not decoded["service"]["name"] == "zammad-pdf-archiver", "assertion failed")
