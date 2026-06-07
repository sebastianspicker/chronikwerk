"""Redis-backed FastAPI lifespan tests."""

from __future__ import annotations

import asyncio

from test.support.checks import check
from test.support.logging_helpers import CapturingWarningLog
from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.jobs import shutdown as shutdown_module
from zammad_pdf_archiver.app.server import create_app, lifespan


def _redis_settings(tmp_path):
    return make_settings(
        str(tmp_path),
        overrides={
            "workflow": {
                "execution_backend": "redis_queue",
                "redis_url": "redis://localhost:6379",
            }
        },
    )


def test_lifespan_with_redis_backend_calls_start_and_stop(tmp_path, monkeypatch) -> None:
    """lifespan with redis_queue backend calls start_queue_worker and stop_queue_worker."""
    app = create_app(settings=_redis_settings(tmp_path))
    started: list[bool] = []
    stopped: list[bool] = []

    async def _fake_start(_s):  # noqa: ANN001
        started.append(True)
        return None

    async def _fake_stop(_s, **_kw) -> None:  # noqa: ANN001
        stopped.append(True)

    async def _fake_aclose() -> int:
        return 0

    async def _fake_wait() -> None:
        pass

    async def _fake_aclose_stores() -> int:
        return 0

    monkeypatch.setattr("zammad_pdf_archiver.app.server.start_queue_worker", _fake_start)
    monkeypatch.setattr("zammad_pdf_archiver.app.server.stop_queue_worker", _fake_stop)
    monkeypatch.setattr("zammad_pdf_archiver.app.server.aclose_queue_clients", _fake_aclose)
    monkeypatch.setattr("zammad_pdf_archiver.app.server.wait_for_tasks", _fake_wait)
    monkeypatch.setattr("zammad_pdf_archiver.app.server.aclose_stores", _fake_aclose_stores)

    async def run() -> None:
        async with lifespan(app):
            pass

    asyncio.run(run())
    check(not not started, "assertion failed")
    check(not not stopped, "assertion failed")


def test_lifespan_shutdown_cleanup_order(tmp_path, monkeypatch) -> None:
    app = create_app(settings=_redis_settings(tmp_path))
    calls: list[str] = []

    async def _fake_start(_s):  # noqa: ANN001
        calls.append("start")
        return None

    async def _fake_stop(_s, **_kw) -> None:  # noqa: ANN001
        check(not shutdown_module.is_shutting_down() is not True, "assertion failed")
        calls.append("stop")

    async def _fake_wait() -> None:
        calls.append("wait")

    async def _fake_aclose_stores() -> int:
        calls.append("stores")
        return 0

    async def _fake_aclose_queue() -> int:
        calls.append("queue")
        return 0

    monkeypatch.setattr("zammad_pdf_archiver.app.server.start_queue_worker", _fake_start)
    monkeypatch.setattr("zammad_pdf_archiver.app.server.stop_queue_worker", _fake_stop)
    monkeypatch.setattr("zammad_pdf_archiver.app.server.wait_for_tasks", _fake_wait)
    monkeypatch.setattr("zammad_pdf_archiver.app.server.aclose_stores", _fake_aclose_stores)
    monkeypatch.setattr("zammad_pdf_archiver.app.server.aclose_queue_clients", _fake_aclose_queue)

    async def run() -> None:
        async with lifespan(app):
            calls.append("body")

    asyncio.run(run())
    check(not not calls == ["start", "body", "stop", "wait", "stores", "queue"], "assertion failed")


def test_lifespan_logs_aggregate_close_failures(tmp_path, monkeypatch) -> None:
    app = create_app(settings=_redis_settings(tmp_path))
    capture = CapturingWarningLog()

    async def _fake_start(_s):  # noqa: ANN001
        return None

    async def _fake_stop(_s, **_kw) -> None:  # noqa: ANN001
        return None

    async def _fake_wait() -> None:
        return None

    async def _fake_aclose_stores() -> int:
        return 2

    async def _fake_aclose_queue() -> int:
        return 1

    monkeypatch.setattr("zammad_pdf_archiver.app.server.start_queue_worker", _fake_start)
    monkeypatch.setattr("zammad_pdf_archiver.app.server.stop_queue_worker", _fake_stop)
    monkeypatch.setattr("zammad_pdf_archiver.app.server.wait_for_tasks", _fake_wait)
    monkeypatch.setattr("zammad_pdf_archiver.app.server.aclose_stores", _fake_aclose_stores)
    monkeypatch.setattr("zammad_pdf_archiver.app.server.aclose_queue_clients", _fake_aclose_queue)
    monkeypatch.setattr("zammad_pdf_archiver.app.server.log", capture)

    async def run() -> None:
        async with lifespan(app):
            pass

    asyncio.run(run())

    check(
        not not capture.warning_events
        == [("shutdown.redis_close_failures", {"store_failures": 2, "queue_failures": 1})],
        "assertion failed",
    )
