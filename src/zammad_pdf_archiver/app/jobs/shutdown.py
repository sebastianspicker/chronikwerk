import asyncio
import threading

import structlog

log = structlog.get_logger(__name__)

_SHUTTING_DOWN = False
_TASKS: set[asyncio.Task] = set()
_TASKS_GUARD = threading.RLock()


def is_shutting_down() -> bool:
    """Return True if the application is in the process of shutting down."""
    return _SHUTTING_DOWN


def set_shutting_down() -> None:
    """Mark the application as shutting down to stop new work from being accepted."""
    global _SHUTTING_DOWN
    with _TASKS_GUARD:
        _SHUTTING_DOWN = True


def clear_shutting_down() -> None:
    global _SHUTTING_DOWN
    with _TASKS_GUARD:
        _SHUTTING_DOWN = False


def track_task(task: asyncio.Task) -> None:
    """Register a background task so it is awaited during graceful shutdown."""
    if task.done():
        return
    with _TASKS_GUARD:
        _TASKS.add(task)
    task.add_done_callback(_discard_task)


def _discard_task(task: asyncio.Task) -> None:
    with _TASKS_GUARD:
        _TASKS.discard(task)
    _log_task_failure(task)


def _log_task_failure(task: asyncio.Task) -> None:
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    except Exception as exc:
        log.warning(
            "shutdown.tracked_task_exception_unavailable",
            task_name=task.get_name(),
            error=f"{exc.__class__.__name__}: {exc}",
        )
        return
    if exc is None:
        return
    log.error(
        "shutdown.tracked_task_failed",
        task_name=task.get_name(),
        error=f"{exc.__class__.__name__}: {exc}",
    )


async def wait_for_tasks(timeout: float = 1.0) -> None:
    """Await all tracked background tasks, cancelling any that exceed the timeout."""
    loop_tasks = _tracked_tasks_for_current_loop()
    if not loop_tasks:
        return
    try:
        await asyncio.wait_for(asyncio.gather(*loop_tasks, return_exceptions=True), timeout=timeout)
    except TimeoutError:
        await _cancel_timed_out_tasks(loop_tasks, timeout=timeout)
    finally:
        _remove_tracked_tasks(loop_tasks)


def _tracked_tasks_for_current_loop() -> set[asyncio.Task]:
    running_loop = asyncio.get_running_loop()
    with _TASKS_GUARD:
        if not _TASKS:
            return set()
        all_loop_tasks = asyncio.all_tasks(running_loop)
        loop_tasks = {task for task in _TASKS if not task.done() and task in all_loop_tasks}
        _TASKS.difference_update(
            {task for task in _TASKS if task.done() or task not in all_loop_tasks}
        )
        return loop_tasks


async def _cancel_timed_out_tasks(loop_tasks: set[asyncio.Task], *, timeout: float) -> None:
    log.warning("shutdown.tracked_tasks_timeout", count=len(loop_tasks), timeout=timeout)
    for task in loop_tasks:
        task.cancel()
    await asyncio.gather(*loop_tasks, return_exceptions=True)


def _remove_tracked_tasks(loop_tasks: set[asyncio.Task]) -> None:
    with _TASKS_GUARD:
        _TASKS.difference_update(loop_tasks)
