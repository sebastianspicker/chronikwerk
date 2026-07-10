from __future__ import annotations

# Directly exercises the private admission boundary of the job orchestration.
# pylint: disable=protected-access,wrong-import-order
# ruff: noqa: I001  # Pylint and Ruff classify the in-repository test package differently.

import asyncio
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.requests import Request

import zammad_pdf_archiver.adapters.pdf.render_pdf as render_module
from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.jobs import process_ticket as process_module
from zammad_pdf_archiver.app.jobs.admission import JobAdmission
from zammad_pdf_archiver.app.jobs.process_ticket import _TicketJobContext
from zammad_pdf_archiver.app.jobs.ticket_storage import StorageResult
from zammad_pdf_archiver.app.routes.ingest import (
    IngestPayload,
    _run_process_ticket_background,
    batch_ingest,
    ingest_webhook,
)
from zammad_pdf_archiver.app.server import create_app
from zammad_pdf_archiver.domain.snapshot_models import Snapshot


def _request(app, path: str = "/ingest") -> Request:
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [],
            "app": app,
        }
    )
    request.state.request_id = "req-admission"
    return request


def test_admission_bounds_reservations_and_running_slots() -> None:
    async def run() -> None:
        admission = JobAdmission(max_pending=1, max_running=1)
        assert admission.try_reserve(2)
        assert not admission.try_reserve()

        first = asyncio.create_task(admission.acquire())
        second = asyncio.create_task(admission.acquire())
        await asyncio.sleep(0)
        assert admission.running == 1
        assert admission.pending == 1

        await admission.close()
        await first
        await admission.release()
        await second
        assert admission.running == 1
        await admission.release()
        assert admission.running == 0

    asyncio.run(run())


def test_admission_settings_are_explicit_and_validated(tmp_path) -> None:
    settings = make_settings(str(tmp_path))
    assert settings.admission.max_pending == 100
    assert settings.admission.max_running == 4
    with pytest.raises(ValueError):
        make_settings(str(tmp_path), overrides={"admission": {"max_running": 0}})


def test_batch_capacity_rejection_is_atomic(tmp_path) -> None:
    async def run() -> None:
        settings = make_settings(
            str(tmp_path),
            overrides={"admission": {"max_pending": 1, "max_running": 1}},
        )
        app = create_app(settings)
        admission = app.state.admission
        assert admission.try_reserve(2)
        response = await batch_ingest(
            _request(app, "/ingest/batch"),
            [IngestPayload(ticket_id=1), IngestPayload(ticket_id=2), IngestPayload(ticket_id=3)],
        )
        assert response.status_code == 503
        assert response.headers["Retry-After"] == "1"
        assert bytes(response.body).find(b"job_capacity_exhausted") >= 0

    asyncio.run(run())


def test_ingest_overload_does_not_create_an_extra_task(tmp_path, monkeypatch) -> None:
    async def run() -> None:
        settings = make_settings(
            str(tmp_path),
            overrides={"admission": {"max_pending": 0, "max_running": 1}},
        )
        app = create_app(settings)
        gate = asyncio.Event()

        async def slow_process(*_args, **_kwargs) -> None:
            await gate.wait()

        monkeypatch.setattr("zammad_pdf_archiver.app.routes.ingest.process_ticket", slow_process)
        first = await ingest_webhook(_request(app), IngestPayload(ticket_id=1))
        assert first.status_code == 202
        await asyncio.sleep(0)
        second = await ingest_webhook(_request(app), IngestPayload(ticket_id=2))
        assert second.status_code == 503
        assert app.state.admission.running == 1
        gate.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(run())


def test_defensive_no_id_and_cancellation_release_reservations(tmp_path, monkeypatch) -> None:
    async def run() -> None:
        settings = make_settings(str(tmp_path))
        admission = JobAdmission(max_pending=1, max_running=1)

        assert admission.try_reserve()
        await _run_process_ticket_background(
            delivery_id=None,
            payload={},
            settings=settings,
            admission=admission,
        )
        assert admission.pending == 0
        assert admission.running == 0

        gate = asyncio.Event()

        async def blocked_process(*_args, **_kwargs) -> None:
            await gate.wait()

        monkeypatch.setattr(
            "zammad_pdf_archiver.app.routes.ingest.process_ticket", blocked_process
        )
        assert admission.try_reserve()
        task = asyncio.create_task(
            _run_process_ticket_background(
                delivery_id=None,
                payload={"ticket_id": 7},
                settings=settings,
                admission=admission,
            )
        )
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert admission.pending == 0
        assert admission.running == 0

    asyncio.run(run())


def test_render_and_storage_leave_event_loop_thread(tmp_path, monkeypatch) -> None:
    async def run() -> None:
        loop_thread = threading.get_ident()
        seen: dict[str, int] = {}

        @contextmanager
        def fake_template_folder():
            yield tmp_path

        monkeypatch.setattr(render_module, "_template_folder_path", fake_template_folder)
        monkeypatch.setattr(render_module, "_css_file_paths", lambda _path: (Path("styles.css"),))
        monkeypatch.setattr(render_module, "render_html", lambda *_args, **_kwargs: "<p>x</p>")

        def fake_write(*_args, **_kwargs):
            seen["render"] = threading.get_ident()
            return b"%PDF"

        monkeypatch.setattr(render_module, "_write_pdf", fake_write)
        snapshot = Snapshot.model_validate({"ticket": {"id": 1, "number": "T1"}, "articles": []})
        assert await render_module.render_pdf(snapshot) == b"%PDF"

        async def fake_build(*_args, **_kwargs):
            return b"%PDF", snapshot

        def fake_store(*_args, **_kwargs):
            seen["storage"] = threading.get_ident()
            return StorageResult(Path("a.pdf"), Path("a.json"), "0" * 64, 4)

        monkeypatch.setattr(process_module, "build_and_render_pdf", fake_build)
        monkeypatch.setattr(process_module, "store_ticket_files", fake_store)
        ctx = _TicketJobContext(
            ticket_id=1,
            settings=make_settings(str(tmp_path)),
            delivery_id=None,
            request_id=None,
        )
        await process_module._render_and_store_ticket(
            client=None,  # type: ignore[arg-type]
            ctx=ctx,
            ticket=SimpleNamespace(id=1),
            tags=[],
            storage_paths=(Path("a.pdf"), Path("a.json")),
            now=datetime.now(UTC),
        )
        assert seen["render"] != loop_thread
        assert seen["storage"] != loop_thread

    asyncio.run(run())
