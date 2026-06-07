"""Shutdown task and Redis worker-stop logging tests."""

from __future__ import annotations

import asyncio

from test.support.checks import check
from test.support.logging_helpers import CapturingWarningLog
from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.jobs import redis_queue as redis_queue_module
from zammad_pdf_archiver.app.jobs import shutdown as shutdown_module


def test_wait_for_tasks_logs_tracked_task_failure(monkeypatch) -> None:
    capture = CapturingWarningLog()
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
    capture = CapturingWarningLog()
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
    capture = CapturingWarningLog()
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
