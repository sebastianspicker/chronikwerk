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
