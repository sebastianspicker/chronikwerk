"""Verifies admission capacity, shutdown, and batch scheduling invariants."""

from __future__ import annotations

import asyncio
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
from starlette.requests import Request

import chronikwerk.adapters.pdf.render_pdf as render_module
from chronikwerk.adapters.zammad.models import TagList, Ticket
from chronikwerk.app.jobs import _ticket_pipeline as process_module
from chronikwerk.app.jobs._ticket_pipeline import TicketJobContext as _TicketJobContext
from chronikwerk.app.jobs.admission import AdmissionClosed, JobAdmission
from chronikwerk.app.jobs.history import read_history, reset_for_tests
from chronikwerk.app.jobs.shutdown import clear_shutting_down, set_shutting_down
from chronikwerk.app.jobs.ticket_renderer import RenderedTicket
from chronikwerk.app.jobs.ticket_storage import StorageResult
from chronikwerk.app.routes.ingest import (
    IngestPayload,
    _run_process_ticket_background,
    _schedule_batch,
    batch_ingest,
    ingest_webhook,
    schedule_retry,
)
from chronikwerk.app.server import create_app
from chronikwerk.domain.snapshot_models import Snapshot
from tests.support.settings_factory import make_settings


def _request(app, path: str = "/ingest") -> Request:
    """Build a deterministic request fixture for focused assertions."""
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
        with pytest.raises(AdmissionClosed):
            await second
        assert admission.pending == 0
        assert admission.running == 1
        assert not admission.try_reserve()

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


def test_retry_is_not_scheduled_after_shutdown_starts(tmp_path) -> None:
    settings = make_settings(str(tmp_path))
    app = create_app(settings)
    set_shutting_down()
    try:
        assert not schedule_retry(_request(app, "/retry/1"), ticket_id=1, settings=settings)
        assert app.state.admission.pending == 0
        assert app.state.admission.running == 0
    finally:
        clear_shutting_down()


def test_batch_is_not_scheduled_after_shutdown_starts(tmp_path) -> None:
    settings = make_settings(str(tmp_path))
    admission = JobAdmission(max_pending=1, max_running=1)
    set_shutting_down()
    try:
        assert not _schedule_batch(
            [(None, {"ticket_id": 1})],
            settings=settings,
            admission=admission,
        )
        assert admission.pending == 0
        assert admission.running == 0
    finally:
        clear_shutting_down()


def test_batch_records_accepted_history_for_each_created_task(tmp_path, monkeypatch) -> None:
    async def run() -> None:
        reset_for_tests()
        settings = make_settings(str(tmp_path))
        app = create_app(settings)
        gate = asyncio.Event()

        async def blocked_process(**_kwargs) -> None:
            await gate.wait()

        monkeypatch.setattr(
            "chronikwerk.app.routes.ingest._run_process_ticket_background",
            blocked_process,
        )
        response = await batch_ingest(
            _request(app, "/ingest/batch"),
            [IngestPayload(ticket_id=11), IngestPayload(ticket_id=12)],
        )

        assert response.status_code == 202
        accepted = read_history(10, statuses={"accepted"})
        assert {item["ticket_id"] for item in accepted} == {11, 12}
        gate.set()
        await asyncio.sleep(0)

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

        monkeypatch.setattr("chronikwerk.app.routes.ingest.process_ticket", slow_process)
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

        monkeypatch.setattr("chronikwerk.app.routes.ingest.process_ticket", blocked_process)
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
            return RenderedTicket(b"%PDF", snapshot, None)

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
        await process_module.render_and_store_ticket(
            client=None,  # type: ignore[arg-type]
            ctx=ctx,
            ticket=Ticket(id=1, number="T1"),
            tags=TagList([]),
            storage_paths=(Path("a.pdf"), Path("a.json")),
            now=datetime.now(UTC),
        )
        assert seen["render"] != loop_thread
        assert seen["storage"] != loop_thread

    asyncio.run(run())


def test_weasyprint_native_entry_is_serialized(tmp_path, monkeypatch) -> None:
    started = threading.Event()
    native_entered = threading.Event()
    result: list[bytes] = []

    def fake_write_unlocked(*_args, **_kwargs) -> bytes:
        native_entered.set()
        return b"%PDF"

    def render_in_worker() -> None:
        started.set()
        result.append(
            render_module._write_pdf(  # noqa: SLF001
                "<p>x</p>",
                template_folder=tmp_path,
                css_paths=(),
            )
        )

    monkeypatch.setattr(render_module, "_write_pdf_unlocked", fake_write_unlocked)
    render_lock = render_module._WEASYPRINT_RENDER_LOCK  # noqa: SLF001
    assert render_lock.acquire(timeout=1.0)
    worker = threading.Thread(target=render_in_worker)
    worker.start()
    try:
        assert started.wait(timeout=1.0)
        assert not native_entered.wait(timeout=0.1)
    finally:
        render_lock.release()
    worker.join(timeout=1.0)

    assert not worker.is_alive()
    assert native_entered.is_set()
    assert result == [b"%PDF"]
