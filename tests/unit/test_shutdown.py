"""Verifies shutdown state and tracked-task lifecycle management."""

from __future__ import annotations

import asyncio

from chronikwerk.app.jobs import shutdown as shutdown_module


def test_initial_state() -> None:
    """GracefulShutdown starts with cancelled=False."""
    shutdown_module.clear_shutting_down()
    try:
        assert shutdown_module.is_shutting_down() is False
    finally:
        shutdown_module.clear_shutting_down()


def test_cancel_sets_flag() -> None:
    """set_shutting_down() sets the flag to True."""
    shutdown_module.clear_shutting_down()
    try:
        shutdown_module.set_shutting_down()
        assert shutdown_module.is_shutting_down() is True
    finally:
        shutdown_module.clear_shutting_down()


def test_register_and_unregister_task() -> None:
    """track_task adds a task; completion removes it via done callback."""

    async def _run() -> None:
        event = asyncio.Event()

        async def _wait_for_event() -> None:
            await event.wait()

        task = asyncio.create_task(_wait_for_event())

        shutdown_module.track_task(task)
        assert task in shutdown_module._TASKS  # noqa: SLF001

        # Complete the task and yield repeatedly so the done callback fires.
        event.set()
        await task
        # Done callbacks are invoked synchronously when the task finishes,
        # but we need to yield so the task actually completes first.
        await asyncio.sleep(0)

        assert task not in shutdown_module._TASKS  # noqa: SLF001

    shutdown_module._TASKS.clear()  # noqa: SLF001
    try:
        asyncio.run(_run())
    finally:
        shutdown_module._TASKS.clear()  # noqa: SLF001


def test_wait_for_tasks_no_tasks() -> None:
    """wait_for_tasks completes immediately when no tasks are tracked."""

    async def _run() -> None:
        await shutdown_module.wait_for_tasks(timeout=0.1)

    shutdown_module._TASKS.clear()  # noqa: SLF001
    try:
        asyncio.run(_run())
    finally:
        shutdown_module._TASKS.clear()  # noqa: SLF001


def test_is_shutting_down() -> None:
    """is_shutting_down reflects the internal flag toggled by set/clear."""
    shutdown_module.clear_shutting_down()
    try:
        assert shutdown_module.is_shutting_down() is False
        shutdown_module.set_shutting_down()
        assert shutdown_module.is_shutting_down() is True
        shutdown_module.clear_shutting_down()
        assert shutdown_module.is_shutting_down() is False
    finally:
        shutdown_module.clear_shutting_down()


def test_track_task_already_done_not_tracked() -> None:
    """track_task with an already-done task should not add it to _TASKS."""

    async def _run() -> None:
        async def _noop() -> None:
            pass

        task = asyncio.create_task(_noop())
        await task  # ensure task is done
        await asyncio.sleep(0)

        assert task.done()

        shutdown_module._TASKS.clear()  # noqa: SLF001
        shutdown_module.track_task(task)
        assert task not in shutdown_module._TASKS  # noqa: SLF001

    shutdown_module._TASKS.clear()  # noqa: SLF001
    try:
        asyncio.run(_run())
    finally:
        shutdown_module._TASKS.clear()  # noqa: SLF001


def test_wait_for_tasks_timeout() -> None:
    """wait_for_tasks cancels tracked tasks when the deadline expires."""

    async def _run() -> None:
        stalled = asyncio.Event()
        cancelled = asyncio.Event()

        async def _never_finish() -> None:
            try:
                await stalled.wait()  # will never be set
            finally:
                cancelled.set()

        task = asyncio.create_task(_never_finish())
        shutdown_module.track_task(task)

        # Use a very short timeout so the test doesn't hang.
        await shutdown_module.wait_for_tasks(timeout=0.01)

        # After timeout the task must be cancelled and removed from tracking.
        assert task.cancelled()
        assert task not in shutdown_module._TASKS  # noqa: SLF001
        assert cancelled.is_set()

    shutdown_module._TASKS.clear()  # noqa: SLF001
    try:
        asyncio.run(_run())
    finally:
        shutdown_module._TASKS.clear()  # noqa: SLF001
