from __future__ import annotations

# pylint: disable=wrong-import-order
# ruff: noqa: I001  # Pylint and Ruff classify the in-repository test package differently.

import asyncio
import threading

import pytest

from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.jobs.shutdown import clear_shutting_down
from zammad_pdf_archiver.app.jobs.ticket_storage import ArchiveRecoveryError
from zammad_pdf_archiver.app.server import create_app, lifespan


def test_create_app_sets_settings(tmp_path) -> None:
    settings = make_settings(str(tmp_path))
    app = create_app(settings=settings)
    assert app.state.settings is settings


def test_lifespan_waits_for_inprocess_tasks(tmp_path, monkeypatch) -> None:
    app = create_app(settings=make_settings(str(tmp_path)))
    called: list[str] = []

    async def _fake_wait(*, timeout: float) -> None:
        called.append("wait")
        assert timeout == 5.0

    async def _fake_close() -> None:
        called.append("close")

    monkeypatch.setattr("zammad_pdf_archiver.app.server.wait_for_tasks", _fake_wait)
    monkeypatch.setattr("zammad_pdf_archiver.app.server.aclose_stores", _fake_close)

    async def run() -> None:
        async with lifespan(app):
            pass

    asyncio.run(run())

    assert called == ["wait", "close"]
    clear_shutting_down()


def test_lifespan_reconciles_storage_off_event_loop_before_yield(
    tmp_path, monkeypatch
) -> None:
    app = create_app(settings=make_settings(str(tmp_path)))
    event_loop_thread = threading.get_ident()
    recovery_threads: list[int] = []

    def _recover(*_args, **_kwargs) -> int:  # noqa: ANN002, ANN003
        recovery_threads.append(threading.get_ident())
        return 0

    monkeypatch.setattr("zammad_pdf_archiver.app.server.recover_archive_transactions", _recover)

    async def run() -> None:
        async with lifespan(app):
            assert recovery_threads

    asyncio.run(run())

    assert recovery_threads[0] != event_loop_thread
    clear_shutting_down()


def test_lifespan_surfaces_storage_recovery_failure_before_yield(
    tmp_path, monkeypatch
) -> None:
    app = create_app(settings=make_settings(str(tmp_path)))
    yielded = False

    def _fail(*_args, **_kwargs) -> int:  # noqa: ANN002, ANN003
        raise ArchiveRecoveryError("unrecoverable archive transaction")

    monkeypatch.setattr("zammad_pdf_archiver.app.server.recover_archive_transactions", _fail)

    async def run() -> None:
        nonlocal yielded
        async with lifespan(app):
            yielded = True

    with pytest.raises(ArchiveRecoveryError, match="unrecoverable archive transaction"):
        asyncio.run(run())

    assert yielded is False
    clear_shutting_down()
