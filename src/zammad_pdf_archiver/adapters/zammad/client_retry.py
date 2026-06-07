from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx

from zammad_pdf_archiver.adapters.zammad.errors import RateLimitError, ServerError

MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 0.2


def backoff_seconds(attempt: int) -> float:
    # attempt is 0-based for retry count, i.e. after the first failure.
    return BACKOFF_BASE_SECONDS * (2**attempt)


async def retry_after_timeout_or_transport(
    *,
    retry_count: int,
    max_attempts: int,
    exc: Exception,
    sleep: Callable[[float], Awaitable[None]],
    timeout_path: str | None = None,
) -> int:
    if retry_count >= MAX_RETRIES:
        if isinstance(exc, httpx.TimeoutException):
            path = timeout_path or "<unknown>"
            raise ServerError(
                f"Zammad API timeout after {max_attempts} attempts at {path}"
            ) from exc
        raise ServerError(f"Network error after {max_attempts} attempts") from exc
    await sleep(backoff_seconds(retry_count))
    return retry_count + 1


def retry_delay_for_response(
    response: httpx.Response,
    *,
    retry_count: int,
    max_attempts: int,
) -> float | None:
    status = response.status_code
    if status >= 500:
        if retry_count >= MAX_RETRIES:
            raise ServerError(
                f"Zammad server error (status={status}) after {max_attempts} attempts"
            )
        return backoff_seconds(retry_count)
    if status == 429:
        if retry_count >= MAX_RETRIES:
            raise RateLimitError(f"Zammad rate limit (status=429) after {max_attempts} attempts")
        retry_after = parse_retry_after_seconds(response.headers.get("Retry-After"))
        return retry_after or backoff_seconds(retry_count)
    return None


def parse_retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    if seconds < 0:
        return None
    return min(seconds, 60)
