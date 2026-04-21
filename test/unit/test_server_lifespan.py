"""Tests for the FastAPI app lifespan function (server.py lines 38-50)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest

from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.jobs.shutdown import clear_shutting_down
from zammad_pdf_archiver.app.server import create_app, lifespan


@pytest.fixture(autouse=True)
def _restore_shutdown_state() -> Iterator[None]:
    """Ensure the global shutting-down flag is cleared after each lifespan test."""
    yield
    clear_shutting_down()


def test_lifespan_with_no_settings() -> None:
    """lifespan with settings=None skips queue worker start/stop."""
    app = create_app(settings=None)

    async def run() -> None:
        async with lifespan(app):
            pass

    asyncio.run(run())


def test_lifespan_with_inprocess_backend(tmp_path) -> None:
    """lifespan with inprocess backend starts no worker (start_queue_worker returns None)."""
    settings = make_settings(
        str(tmp_path),
        overrides={"workflow": {"execution_backend": "inprocess"}},
    )
    app = create_app(settings=settings)

    async def run() -> None:
        async with lifespan(app):
            pass

    asyncio.run(run())


def test_lifespan_with_redis_backend_calls_start_and_stop(tmp_path, monkeypatch) -> None:
    """lifespan with redis_queue backend calls start_queue_worker and stop_queue_worker."""
    settings = make_settings(
        str(tmp_path),
        overrides={
            "workflow": {
                "execution_backend": "redis_queue",
                "redis_url": "redis://localhost:6379",
            }
        },
    )
    app = create_app(settings=settings)

    started: list[bool] = []
    stopped: list[bool] = []

    async def _fake_start(_s):  # noqa: ANN001
        started.append(True)
        return None

    async def _fake_stop(_s, **_kw) -> None:  # noqa: ANN001
        stopped.append(True)

    async def _fake_aclose() -> None:
        pass

    async def _fake_wait() -> None:
        pass

    async def _fake_aclose_stores() -> None:
        pass

    monkeypatch.setattr("zammad_pdf_archiver.app.server.start_queue_worker", _fake_start)
    monkeypatch.setattr("zammad_pdf_archiver.app.server.stop_queue_worker", _fake_stop)
    monkeypatch.setattr("zammad_pdf_archiver.app.server.aclose_queue_clients", _fake_aclose)
    monkeypatch.setattr("zammad_pdf_archiver.app.server.wait_for_tasks", _fake_wait)
    monkeypatch.setattr("zammad_pdf_archiver.app.server.aclose_stores", _fake_aclose_stores)

    async def run() -> None:
        async with lifespan(app):
            pass

    asyncio.run(run())
    assert started
    assert stopped
