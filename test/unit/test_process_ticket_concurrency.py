from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from test.support.process_ticket_cleanup_helpers import (
    TagList,
    _patch_process_ticket_client,
    _patch_process_ticket_render_pdf,
    _settings,
    _TagSetProcessTicketClient,
    asyncio,
    check,
    process_ticket,
    ticket_stores,
)


def test_process_ticket_serializes_same_ticket_concurrent_runs(monkeypatch, tmp_path: Path) -> None:
    ticket_stores._reset_for_tests()

    class _FakeClient(_TagSetProcessTicketClient):
        _notes_written = 0
        ticket_title = "concurrency"

        async def list_tags(self, ticket_id: int) -> TagList:  # noqa: ARG002
            # Snapshot before yielding: two concurrent calls both see trigger tag.
            snapshot = sorted(type(self)._tags)
            await asyncio.sleep(0.05)
            return TagList(snapshot)

        async def create_internal_article(
            self,
            ticket_id: int,
            subject: str,
            body_html: str,  # noqa: ARG002
        ) -> SimpleNamespace:
            type(self)._notes_written += 1
            return SimpleNamespace(id=type(self)._notes_written)

    async def _fake_build_and_render_pdf(
        client,
        ticket,
        tags,
        ticket_id: int,
        settings,  # noqa: ANN001, ARG001
    ) -> tuple[bytes, SimpleNamespace, bool, int]:
        return b"%PDF-1.7\n%%EOF\n", SimpleNamespace(ticket=ticket), False, 0

    pdf_writes: list[Path] = []

    def _fake_store_ticket_files(*args, **kwargs) -> SimpleNamespace:  # noqa: ANN002, ANN003
        target_path = kwargs["target_path"]
        pdf_bytes = kwargs["pdf_bytes"]
        check(not not isinstance(target_path, Path), "assertion failed")
        check(not not isinstance(pdf_bytes, bytes), "assertion failed")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(pdf_bytes)
        pdf_writes.append(target_path)
        return SimpleNamespace(
            target_path=target_path,
            sidecar_path=target_path.with_suffix(".pdf.json"),
            sha256_hex="deadbeef",
            size_bytes=len(pdf_bytes),
        )

    _patch_process_ticket_client(monkeypatch, _FakeClient)
    _patch_process_ticket_render_pdf(monkeypatch, _fake_build_and_render_pdf)
    monkeypatch.setattr(
        "zammad_pdf_archiver.app.jobs.process_ticket.store_ticket_files",
        _fake_store_ticket_files,
    )

    settings = _settings(tmp_path)
    payload = {"ticket": {"id": 123}}

    async def _run() -> None:
        await asyncio.gather(
            process_ticket("d-1", payload, settings),
            process_ticket("d-2", payload, settings),
        )

    asyncio.run(_run())

    check(not not _FakeClient._notes_written == 1, "assertion failed")
    check(not not len(pdf_writes) == 1, "assertion failed")
    check(not not pdf_writes[0].is_file(), "assertion failed")
    check(not not pdf_writes[0].read_bytes().startswith(b"%PDF"), "assertion failed")
