from __future__ import annotations

import asyncio
import os
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from zammad_pdf_archiver.config.settings import Settings


@dataclass(frozen=True)
class WorkerLoopDeps:
    get_redis: Callable[[Settings], Awaitable[Any]]
    ensure_group: Callable[..., Awaitable[None]]
    claim_stale_pending: Callable[..., Awaitable[list[tuple[Any, Any]]]]
    read_own_pending: Callable[..., Awaitable[list[tuple[Any, Any]]]]
    read_new_messages: Callable[..., Awaitable[list[tuple[Any, Any]]]]
    process_messages: Callable[..., Awaitable[float | None]]
    sleep: Callable[[float], Awaitable[None]]
    log: Any


def backend(settings: Settings) -> str:
    return (settings.workflow.execution_backend or "inprocess").strip().lower()


def consumer_name(settings: Settings) -> str:
    configured = settings.workflow.queue_consumer
    if configured and configured.strip():
        return configured.strip()
    return f"{socket.gethostname()}-{os.getpid()}"


async def worker_loop(
    settings: Settings,
    stop_event: asyncio.Event,
    *,
    deps: WorkerLoopDeps,
) -> None:
    """Main consumer loop: claim stale, process pending, then poll for new messages."""
    redis = await deps.get_redis(settings)
    consumer = consumer_name(settings)
    stream = settings.workflow.queue_stream
    group = settings.workflow.queue_group
    await deps.ensure_group(redis, stream=stream, group=group)

    consecutive_failures = 0

    while not stop_event.is_set():
        try:
            min_delay = await run_worker_iteration(
                redis,
                settings=settings,
                consumer=consumer,
                deps=deps,
            )
            consecutive_failures = 0

            if min_delay is not None and min_delay > 0:
                await deps.sleep(min(min_delay, 1.0))
        except asyncio.CancelledError:  # pragma: no cover
            raise
        # Fail loud, probe Redis, back off, then keep the worker alive.
        except Exception:
            consecutive_failures += 1
            deps.log.exception(
                "queue.worker.loop_error", consecutive_failures=consecutive_failures
            )
            await backoff_after_worker_error(redis, consecutive_failures, deps=deps)


async def run_worker_iteration(
    redis: Any,
    *,
    settings: Settings,
    consumer: str,
    deps: WorkerLoopDeps,
) -> float | None:
    stream = settings.workflow.queue_stream
    group = settings.workflow.queue_group
    read_count = settings.workflow.queue_read_count

    claimed = await deps.claim_stale_pending(
        redis,
        stream=stream,
        group=group,
        consumer=consumer,
        count=read_count,
    )
    claimed_delay = await deps.process_messages(redis, settings=settings, messages=claimed)

    pending = await deps.read_own_pending(
        redis,
        stream=stream,
        group=group,
        consumer=consumer,
        count=read_count,
    )
    pending_delay = await deps.process_messages(redis, settings=settings, messages=pending)

    block_ms = 1 if (claimed or pending) else settings.workflow.queue_read_block_ms
    new_messages = await deps.read_new_messages(
        redis,
        stream=stream,
        group=group,
        consumer=consumer,
        count=read_count,
        block_ms=block_ms,
    )
    new_delay = await deps.process_messages(redis, settings=settings, messages=new_messages)
    return minimum_positive_delay(claimed_delay, pending_delay, new_delay)


def minimum_positive_delay(*delays: float | None) -> float | None:
    positive = [delay for delay in delays if delay is not None and delay > 0]
    if not positive:
        return None
    return min(positive)


async def backoff_after_worker_error(
    redis: Any, consecutive_failures: int, *, deps: WorkerLoopDeps
) -> None:
    try:
        await redis.ping()
    except Exception:
        deps.log.error(
            "queue.worker.redis_unreachable",
            detail="Redis ping failed; connection may be stale.",
            consecutive_failures=consecutive_failures,
        )

    backoff = min(0.3 * (2 ** (consecutive_failures - 1)), 30.0)
    await deps.sleep(backoff)


async def wait_for_worker_stop(task: asyncio.Task[None], *, timeout: float, log: Any) -> None:
    done, _pending = await asyncio.wait({task}, timeout=timeout)
    if not done:
        await cancel_timed_out_worker(task, timeout=timeout, log=log)
        return

    await observe_stopped_worker(task, failure_event="queue.worker.stop_failed", log=log)


async def cancel_timed_out_worker(
    task: asyncio.Task[None], *, timeout: float, log: Any
) -> None:
    log.warning("queue.worker.stop_timeout", timeout=timeout)
    task.cancel()
    await observe_stopped_worker(
        task,
        failure_event="queue.worker.stop_failed_after_cancel",
        log=log,
    )


async def observe_stopped_worker(
    task: asyncio.Task[None], *, failure_event: str, log: Any
) -> None:
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:
        log.exception(failure_event)
