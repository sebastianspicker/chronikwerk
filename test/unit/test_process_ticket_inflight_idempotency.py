from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from test.support.process_ticket_cleanup_helpers import (
    Settings,
    TagList,
    TransientError,
    _patch_process_ticket_client,
    _patch_process_ticket_render_pdf,
    _settings,
    _TagSetProcessTicketClient,
    asyncio,
    check,
    process_ticket,
    ticket_stores,
)


def _patch_process_ticket_dependencies(
    monkeypatch,
    tmp_path: Path,
    client_type: type,
) -> dict[str, int]:
    calls = {"n": 0}

    async def _flaky_build_and_render_pdf(
        client,
        ticket,
        tags,
        ticket_id: int,
        settings,  # noqa: ANN001, ARG001
    ) -> tuple[bytes, SimpleNamespace, bool, int]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise TransientError("transient-render-failure")
        return b"%PDF-1.7\n%%EOF\n", SimpleNamespace(ticket=ticket), False, 0

    def _fake_store_ticket_files(*args, **kwargs) -> SimpleNamespace:  # noqa: ANN002, ANN003
        target_path = tmp_path / "archived.pdf"
        return SimpleNamespace(
            target_path=target_path,
            sidecar_path=target_path.with_suffix(".pdf.json"),
            sha256_hex="deadbeef",
            size_bytes=42,
        )

    _patch_process_ticket_client(monkeypatch, client_type)
    _patch_process_ticket_render_pdf(monkeypatch, _flaky_build_and_render_pdf)
    monkeypatch.setattr(
        "zammad_pdf_archiver.app.jobs.process_ticket.store_ticket_files",
        _fake_store_ticket_files,
    )
    return calls


async def _process_concurrent_deliveries(
    payload: dict[str, Any],
    settings: Settings,
) -> None:
    await asyncio.gather(
        process_ticket("d-1", payload, settings),
        process_ticket("d-2", payload, settings),
    )


def test_skipped_inflight_delivery_id_is_not_poisoned_for_retry(
    monkeypatch,
    tmp_path: Path,
) -> None:
    ticket_stores._reset_for_tests()

    class _FakeClient(_TagSetProcessTicketClient):
        _success_notes = 0
        _error_notes = 0
        ticket_title = "idempotency"

        async def list_tags(self, ticket_id: int) -> TagList:  # noqa: ARG002
            await asyncio.sleep(0.05)
            return TagList(sorted(type(self)._tags))

        async def create_internal_article(
            self,
            ticket_id: int,
            subject: str,
            body_html: str,  # noqa: ARG002
        ) -> SimpleNamespace:
            if "archiver error" in subject:
                type(self)._error_notes += 1
            if "PDF archived" in subject:
                type(self)._success_notes += 1
            return SimpleNamespace(id=type(self)._error_notes + type(self)._success_notes)

    _patch_process_ticket_dependencies(monkeypatch, tmp_path, _FakeClient)
    settings = _settings(tmp_path)
    payload = {"ticket": {"id": 321}}

    asyncio.run(_process_concurrent_deliveries(payload, settings))

    # Retry delivery d-2 after the in-flight run is over.
    asyncio.run(process_ticket("d-2", payload, settings))

    # Expected: first run writes one error note; retry run succeeds and writes one success note.
    check(not not _FakeClient._error_notes == 1, "assertion failed")
    check(not not _FakeClient._success_notes == 1, "assertion failed")
