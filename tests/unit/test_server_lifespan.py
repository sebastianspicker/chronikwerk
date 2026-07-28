"""Verifies application lifespan initializes settings and drains in-process work."""

from __future__ import annotations

import asyncio

from chronikwerk.app.jobs.shutdown import clear_shutting_down
from chronikwerk.app.server import create_app, lifespan
from tests.support.settings_factory import make_settings


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

    monkeypatch.setattr("chronikwerk.app.server.wait_for_tasks", _fake_wait)
    monkeypatch.setattr("chronikwerk.app.server.aclose_stores", _fake_close)

    async def run() -> None:
        async with lifespan(app):
            pass

    asyncio.run(run())

    assert called == ["wait", "close"]
    clear_shutting_down()
