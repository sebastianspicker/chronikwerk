import asyncio

_SHUTTING_DOWN = False
_TASKS: set[asyncio.Task] = set()


def is_shutting_down() -> bool:
    """Return True if the application is in the process of shutting down."""
    return _SHUTTING_DOWN


def set_shutting_down() -> None:
    """Mark the application as shutting down to stop new work from being accepted."""
    global _SHUTTING_DOWN
    _SHUTTING_DOWN = True


def clear_shutting_down() -> None:
    global _SHUTTING_DOWN
    _SHUTTING_DOWN = False


def track_task(task: asyncio.Task) -> None:
    """Register a background task so it is awaited during graceful shutdown."""
    if task.done():
        return
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


async def wait_for_tasks(timeout: float = 1.0) -> None:
    """Await all tracked background tasks, cancelling any that exceed the timeout."""
    if not _TASKS:
        return
    running_loop = asyncio.get_running_loop()
    loop_tasks = {t for t in _TASKS if not t.done() and t.get_loop() is running_loop}
    _TASKS.difference_update({t for t in _TASKS if t.done() or t.get_loop() is not running_loop})
    if not loop_tasks:
        return
    try:
        await asyncio.wait_for(asyncio.gather(*loop_tasks, return_exceptions=True), timeout=timeout)
    except TimeoutError:
        for task in loop_tasks:
            task.cancel()
    finally:
        _TASKS.difference_update(loop_tasks)
