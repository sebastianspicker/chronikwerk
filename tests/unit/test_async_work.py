"""Verifies cancellation does not detach synchronous work from its caller."""

from __future__ import annotations

import asyncio
import threading

import pytest

from chronikwerk.domain.async_work import run_sync_cancellation_safe


def test_sync_work_finishes_before_cancellation_propagates() -> None:
    async def _run() -> None:
        started = threading.Event()
        finish = threading.Event()

        def blocking_work() -> None:
            started.set()
            finish.wait(timeout=2.0)

        task = asyncio.create_task(run_sync_cancellation_safe(blocking_work))
        while not started.is_set():
            await asyncio.sleep(0)

        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()

        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_run())


def test_repeated_cancellation_does_not_detach_sync_work() -> None:
    async def _run() -> None:
        started = threading.Event()
        finish = threading.Event()
        finished = threading.Event()

        def blocking_work() -> None:
            started.set()
            finish.wait(timeout=2.0)
            finished.set()

        task = asyncio.create_task(run_sync_cancellation_safe(blocking_work))
        while not started.is_set():
            await asyncio.sleep(0)

        try:
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()

            task.cancel()
            done, _ = await asyncio.wait({task}, timeout=0.05)
            assert not done
        finally:
            finish.set()

        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set()

    asyncio.run(_run())
