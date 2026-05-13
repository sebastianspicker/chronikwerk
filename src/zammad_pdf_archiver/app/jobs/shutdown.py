import asyncio
import threading

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


async def wait_for_tasks(timeout: float = 1.0) -> None:
    """Await all tracked background tasks, cancelling any that exceed the timeout."""
    running_loop = asyncio.get_running_loop()
    with _TASKS_GUARD:
        if not _TASKS:
            return
        all_loop_tasks = asyncio.all_tasks(running_loop)
        loop_tasks = {t for t in _TASKS if not t.done() and t in all_loop_tasks}
        _TASKS.difference_update({t for t in _TASKS if t.done() or t not in all_loop_tasks})
    if not loop_tasks:
        return
    try:
        await asyncio.wait_for(asyncio.gather(*loop_tasks, return_exceptions=True), timeout=timeout)
    except TimeoutError:
        for task in loop_tasks:
            task.cancel()
    finally:
        with _TASKS_GUARD:
            _TASKS.difference_update(loop_tasks)
