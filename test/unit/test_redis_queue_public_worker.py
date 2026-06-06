from __future__ import annotations

import asyncio
from typing import Any

from test.support.checks import check
from test.support.redis_queue_helpers import assert_acked_and_deleted
from test.support.redis_queue_processing_helpers import (
    WorkerRedis,
    make_redis_settings,
    reset_worker_state,
    sleeping_worker_loop,
    wait_for_worker_ack,
)
from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.jobs import redis_queue
from zammad_pdf_archiver.app.jobs.process_ticket import ProcessTicketResult


def test_start_queue_worker_returns_none_for_inprocess(tmp_path) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={"workflow": {"execution_backend": "inprocess"}},
    )

    async def _run() -> None:
        reset_worker_state()
        check(not await redis_queue.start_queue_worker(settings) is not None, "assertion failed")

    asyncio.run(_run())


def test_start_queue_worker_creates_task(monkeypatch, tmp_path) -> None:
    settings = make_redis_settings(tmp_path)

    async def _run() -> asyncio.Task[None] | None:
        reset_worker_state()
        monkeypatch.setattr(redis_queue, "_worker_loop", sleeping_worker_loop)
        task = await redis_queue.start_queue_worker(settings)
        check(not not task is not None, "assertion failed")
        check(not not isinstance(task, asyncio.Task), "assertion failed")
        await redis_queue.stop_queue_worker(settings, timeout=0.1)
        return task

    asyncio.run(_run())


def test_stop_queue_worker_signals_stop(monkeypatch, tmp_path) -> None:
    settings = make_redis_settings(tmp_path)

    async def _run() -> None:
        reset_worker_state()
        stop_observed = False

        async def _fake_worker_loop(s: Any, stop_event: asyncio.Event) -> None:  # noqa: ARG001
            nonlocal stop_observed
            await stop_event.wait()
            stop_observed = True

        monkeypatch.setattr(redis_queue, "_worker_loop", _fake_worker_loop)
        await redis_queue.start_queue_worker(settings)
        await redis_queue.stop_queue_worker(settings, timeout=2.0)
        check(not not stop_observed, "assertion failed")
        check(not redis_queue._worker_task is not None, "assertion failed")  # noqa: SLF001
        check(not redis_queue._worker_stop_event is not None, "assertion failed")  # noqa: SLF001

    asyncio.run(_run())


def test_start_queue_worker_reuses_existing(monkeypatch, tmp_path) -> None:
    settings = make_redis_settings(tmp_path)

    async def _run() -> None:
        reset_worker_state()
        monkeypatch.setattr(redis_queue, "_worker_loop", sleeping_worker_loop)
        task1 = await redis_queue.start_queue_worker(settings)
        task2 = await redis_queue.start_queue_worker(settings)
        check(not task1 is not task2, "assertion failed")
        await redis_queue.stop_queue_worker(settings, timeout=0.1)

    asyncio.run(_run())


def test_public_worker_consumes_enqueued_message(monkeypatch, tmp_path) -> None:
    settings = make_redis_settings(tmp_path, queue_read_block_ms=100)
    fake = WorkerRedis()
    processed: list[tuple[str | None, dict[str, Any]]] = []

    async def _fake_get_redis(_settings: Any) -> WorkerRedis:
        return fake

    async def _fake_process_ticket(
        delivery_id: str | None,
        payload: dict[str, Any],
        _settings: Any,
    ) -> ProcessTicketResult:
        processed.append((delivery_id, payload))
        return ProcessTicketResult(status="processed", ticket_id=payload.get("ticket_id"))

    async def _run() -> None:
        redis_queue._worker_task = None  # noqa: SLF001
        redis_queue._worker_stop_event = None  # noqa: SLF001

        message_id = await redis_queue.enqueue_ticket_job(
            delivery_id="d-public-worker",
            payload={"ticket_id": 42},
            settings=settings,
        )
        check(not not message_id == "1-0", "assertion failed")

        task = await redis_queue.start_queue_worker(settings)
        check(not not task is not None, "assertion failed")
        try:
            await wait_for_worker_ack(fake)
        finally:
            await redis_queue.stop_queue_worker(settings, timeout=1.0)

        if task is None:
            raise AssertionError("assertion failed")
        check(not not task.done(), "assertion failed")

    monkeypatch.setattr(redis_queue, "_get_redis", _fake_get_redis)
    monkeypatch.setattr(redis_queue, "process_ticket", _fake_process_ticket)

    asyncio.run(_run())

    check(
        not not fake.groups_created
        == [(settings.workflow.queue_stream, settings.workflow.queue_group, "0", True)],
        "assertion failed",
    )
    check(not not processed == [("d-public-worker", {"ticket_id": 42})], "assertion failed")
    assert_acked_and_deleted(fake, settings, "1-0")


def test_aclose_queue_clients_delegates(monkeypatch) -> None:
    calls: list[bool] = []

    async def _fake_close_all() -> None:
        calls.append(True)

    monkeypatch.setattr("zammad_pdf_archiver.adapters.redis_pool.close_all", _fake_close_all)

    asyncio.run(redis_queue.aclose_queue_clients())
    check(not not calls == [True], "assertion failed")
