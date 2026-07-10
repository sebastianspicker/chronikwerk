from __future__ import annotations

import asyncio
import hashlib
import json

import respx

from test.support.process_ticket_helpers import (  # pylint: disable=wrong-import-order
    archive_article_json,
    expected_process_ticket_pdf_path,
    fixed_process_ticket_now,
    process_ticket_request_payload,
    process_ticket_settings,
    register_process_ticket_article_route,
    register_process_ticket_fetch_routes,
    register_process_ticket_tag_routes,
)
from zammad_pdf_archiver.app.jobs import process_ticket as process_ticket_module
from zammad_pdf_archiver.app.jobs.process_ticket import process_ticket


def test_audit_sidecar_written_next_to_pdf_and_matches_sha256(tmp_path, monkeypatch) -> None:
    settings = process_ticket_settings(tmp_path)
    fixed_now = fixed_process_ticket_now(monkeypatch, process_ticket_module)
    payload = process_ticket_request_payload("req-audit-1")

    with respx.mock:
        register_process_ticket_fetch_routes(articles=[archive_article_json()])
        register_process_ticket_tag_routes()
        article_route = register_process_ticket_article_route(
            {"id": 999, "internal": True, "subject": "ok", "body": "<p>ok</p>"}
        )

        asyncio.run(process_ticket("delivery-audit-1", payload, settings))

        expected_pdf_path = expected_process_ticket_pdf_path(tmp_path, settings, fixed_now)
        expected_sidecar_path = expected_pdf_path.parent / (expected_pdf_path.name + ".json")

        assert expected_pdf_path.exists()
        assert expected_sidecar_path.exists()

        pdf_bytes = expected_pdf_path.read_bytes()
        sha256_hex = hashlib.sha256(pdf_bytes).hexdigest()

        audit = json.loads(expected_sidecar_path.read_text("utf-8"))
        assert audit["sha256"] == sha256_hex
        assert audit["storage_path"] == str(expected_pdf_path)
        assert audit["ticket_id"] == 123
        assert audit["ticket_number"] == "20240123"

        assert article_route.called
        posted = json.loads(article_route.calls[0].request.content.decode("utf-8"))
        assert sha256_hex in posted["body"]
        assert str(expected_sidecar_path) in posted["body"]
