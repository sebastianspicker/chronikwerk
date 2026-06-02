"""Tests for the FastAPI app lifespan function (server.py lines 38-50)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest

from test.support.checks import check
from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.jobs import redis_queue as redis_queue_module
from zammad_pdf_archiver.app.jobs import shutdown as shutdown_module
from zammad_pdf_archiver.app.jobs.shutdown import clear_shutting_down
from zammad_pdf_archiver.app.server import create_app, lifespan


class _CapturingLog:
    def __init__(self) -> None:
        self.error_events: list[tuple[str, dict[str, object]]] = []
        self.exception_events: list[tuple[str, dict[str, object]]] = []
        self.warning_events: list[tuple[str, dict[str, object]]] = []

    def error(self, event: str, **kwargs: object) -> None:
        self.error_events.append((event, kwargs))

    def exception(self, event: str, **kwargs: object) -> None:
        self.exception_events.append((event, kwargs))

    def warning(self, event: str, **kwargs: object) -> None:
        self.warning_events.append((event, kwargs))


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
    capture = _CapturingLog()

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


def test_wait_for_tasks_logs_tracked_task_failure(monkeypatch) -> None:
    capture = _CapturingLog()
    monkeypatch.setattr(shutdown_module, "log", capture)

    async def run() -> None:
        async def _boom() -> None:
            raise RuntimeError("background failed")

        task = asyncio.create_task(_boom(), name="failing-background-task")
        shutdown_module.track_task(task)
        await asyncio.sleep(0)
        await shutdown_module.wait_for_tasks(timeout=0.1)

    asyncio.run(run())

    check(
        not not capture.error_events
        == [
            (
                "shutdown.tracked_task_failed",
                {
                    "task_name": "failing-background-task",
                    "error": "RuntimeError: background failed",
                },
            )
        ],
        "assertion failed",
    )


def test_wait_for_tasks_logs_timeout(monkeypatch) -> None:
    capture = _CapturingLog()
    monkeypatch.setattr(shutdown_module, "log", capture)

    async def run() -> None:
        async def _slow() -> None:
            await asyncio.Event().wait()

        task = asyncio.create_task(_slow(), name="slow-background-task")
        shutdown_module.track_task(task)
        await shutdown_module.wait_for_tasks(timeout=0.01)

    asyncio.run(run())

    check(
        not not capture.warning_events
        == [("shutdown.tracked_tasks_timeout", {"count": 1, "timeout": 0.01})],
        "assertion failed",
    )


def test_stop_queue_worker_logs_timeout_and_cancel_failure(tmp_path, monkeypatch) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={
            "workflow": {
                "execution_backend": "redis_queue",
                "redis_url": "redis://localhost:6379",
            }
        },
    )
    capture = _CapturingLog()
    monkeypatch.setattr(redis_queue_module, "log", capture)

    async def run() -> None:
        async def _raises_after_cancel() -> None:
            try:
                await asyncio.sleep(999)
            except asyncio.CancelledError as exc:
                raise RuntimeError("worker cleanup failed") from exc

        redis_queue_module._worker_task = asyncio.create_task(_raises_after_cancel())  # noqa: SLF001
        redis_queue_module._worker_stop_event = asyncio.Event()  # noqa: SLF001
        await redis_queue_module.stop_queue_worker(settings, timeout=0.01)

    try:
        asyncio.run(run())
    finally:
        redis_queue_module._worker_task = None  # noqa: SLF001
        redis_queue_module._worker_stop_event = None  # noqa: SLF001

    check(
        not not capture.warning_events == [("queue.worker.stop_timeout", {"timeout": 0.01})],
        "assertion failed",
    )
    check(
        not not capture.exception_events == [("queue.worker.stop_failed_after_cancel", {})],
        "assertion failed",
    )
