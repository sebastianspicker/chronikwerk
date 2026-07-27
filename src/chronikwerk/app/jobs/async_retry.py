"""Retry asynchronous operations with cancellation-safe backoff."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable


async def async_retry[T](
    coro_factory: Callable[[], Awaitable[T]],
    *,
    max_retries: int = 3,
    backoff_base: float = 0.5,
    backoff_factor: float = 2.0,
) -> T:
    """Retry an async operation with exponential backoff.

    Calls coro_factory() up to max_retries + 1 times. On failure, waits
    backoff_base * (backoff_factor ** attempt) seconds before retrying.
    Raises the last exception if all attempts fail.
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return await coro_factory()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            last_exc = exc
            if attempt < max_retries:
                await asyncio.sleep(backoff_base * (backoff_factor**attempt))
    if last_exc is None:
        raise ValueError("max_retries must be >= 0")
    raise last_exc
