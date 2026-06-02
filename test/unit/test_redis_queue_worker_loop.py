from __future__ import annotations

import pytest

from test.unit.test_redis_queue import check, make_settings, redis_queue


def test_worker_loop_one_iteration(monkeypatch, tmp_path) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={
            "workflow": {
                "execution_backend": "redis_queue",
                "redis_url": "redis://localhost:6379",
                "queue_read_block_ms": 100,
            }
        },
    )

    class _FullFakeRedis:
        async def ping(self) -> None:
            pass

    fake_redis = _FullFakeRedis()

    async def fake_get_redis(_settings):  # noqa: ANN001
        return fake_redis

    async def fake_ensure_group(redis, *, stream, group) -> None:  # noqa: ANN001, ARG001
        pass

    async def fake_claim(*args, **kwargs):  # noqa: ANN002, ANN003
        return []

    async def fake_read_own(*args, **kwargs):  # noqa: ANN002, ANN003
        return []

    async def fake_process(redis, *, settings, messages):  # noqa: ANN001, ARG001
        return None

    stop_event = redis_queue.asyncio.Event()

    async def fake_read_new(*args, **kwargs):  # noqa: ANN002, ANN003
        stop_event.set()
        return []

    monkeypatch.setattr(redis_queue, "_get_redis", fake_get_redis)
    monkeypatch.setattr(redis_queue, "_ensure_group", fake_ensure_group)
    monkeypatch.setattr(redis_queue, "_claim_stale_pending", fake_claim)
    monkeypatch.setattr(redis_queue, "_read_own_pending", fake_read_own)
    monkeypatch.setattr(redis_queue, "_read_new_messages", fake_read_new)
    monkeypatch.setattr(redis_queue, "_process_messages", fake_process)

    redis_queue.asyncio.run(redis_queue._worker_loop(settings, stop_event))  # noqa: SLF001


def test_worker_loop_surfaces_stale_pending_claim_failure(monkeypatch, tmp_path) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={
            "workflow": {
                "execution_backend": "redis_queue",
                "redis_url": "redis://localhost:6379",
                "queue_read_block_ms": 100,
            }
        },
    )

    class _FullFakeRedis:
        def __init__(self) -> None:
            self.ping_calls = 0

        async def ping(self) -> None:
            self.ping_calls += 1

    class _CapturingLog:
        def __init__(self) -> None:
            self.exception_events: list[str] = []

        def exception(self, event: str, **kwargs) -> None:  # noqa: ANN003
            self.exception_events.append(event)

        def error(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            return None

    fake_redis = _FullFakeRedis()
    stop_event = redis_queue.asyncio.Event()
    delays: list[float] = []
    capturing_log = _CapturingLog()

    async def fake_get_redis(_settings):  # noqa: ANN001
        return fake_redis

    async def fake_ensure_group(redis, *, stream, group) -> None:  # noqa: ANN001, ARG001
        return None

    async def fake_claim(*args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("pending scan failed")

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)
        stop_event.set()

    monkeypatch.setattr(redis_queue, "_get_redis", fake_get_redis)
    monkeypatch.setattr(redis_queue, "_ensure_group", fake_ensure_group)
    monkeypatch.setattr(redis_queue, "_claim_stale_pending", fake_claim)
    monkeypatch.setattr(redis_queue, "log", capturing_log)
    monkeypatch.setattr(redis_queue.asyncio, "sleep", fake_sleep)

    redis_queue.asyncio.run(redis_queue._worker_loop(settings, stop_event))  # noqa: SLF001

    check(not not capturing_log.exception_events == ["queue.worker.loop_error"], "assertion failed")
    check(not not fake_redis.ping_calls == 1, "assertion failed")
    check(not not delays == [0.3], "assertion failed")


def test_worker_loop_backoff_counter_resets_after_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={
            "workflow": {
                "execution_backend": "redis_queue",
                "redis_url": "redis://localhost:6379",
                "queue_read_block_ms": 100,
            }
        },
    )

    class _FullFakeRedis:
        def __init__(self) -> None:
            self.ping_calls = 0

        async def ping(self) -> None:
            self.ping_calls += 1

    fake_redis = _FullFakeRedis()
    stop_event = redis_queue.asyncio.Event()
    delays: list[float] = []
    claim_outcomes = ["fail", "fail", "success", "fail"]

    async def fake_get_redis(_settings):  # noqa: ANN001
        return fake_redis

    async def fake_ensure_group(redis, *, stream, group) -> None:  # noqa: ANN001, ARG001
        return None

    async def fake_claim(*args, **kwargs):  # noqa: ANN002, ANN003
        outcome = claim_outcomes.pop(0)
        if outcome == "fail":
            raise RuntimeError("pending scan failed")
        return []

    async def fake_read_own(*args, **kwargs):  # noqa: ANN002, ANN003
        return []

    async def fake_read_new(*args, **kwargs):  # noqa: ANN002, ANN003
        return []

    async def fake_process(redis, *, settings, messages):  # noqa: ANN001, ARG001
        return None

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)
        if len(delays) == 3:
            stop_event.set()

    monkeypatch.setattr(redis_queue, "_get_redis", fake_get_redis)
    monkeypatch.setattr(redis_queue, "_ensure_group", fake_ensure_group)
    monkeypatch.setattr(redis_queue, "_claim_stale_pending", fake_claim)
    monkeypatch.setattr(redis_queue, "_read_own_pending", fake_read_own)
    monkeypatch.setattr(redis_queue, "_read_new_messages", fake_read_new)
    monkeypatch.setattr(redis_queue, "_process_messages", fake_process)
    monkeypatch.setattr(redis_queue.asyncio, "sleep", fake_sleep)

    redis_queue.asyncio.run(redis_queue._worker_loop(settings, stop_event))  # noqa: SLF001

    check(not not fake_redis.ping_calls == 3, "assertion failed")
    check(not not len(delays) == 3, "assertion failed")
    check(not not delays[1] > delays[0], "assertion failed")
    check(not not delays[2] == delays[0], "assertion failed")


def test_worker_loop_backoff_delay_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={
            "workflow": {
                "execution_backend": "redis_queue",
                "redis_url": "redis://localhost:6379",
                "queue_read_block_ms": 100,
            }
        },
    )

    class _FullFakeRedis:
        async def ping(self) -> None:
            return None

    fake_redis = _FullFakeRedis()
    stop_event = redis_queue.asyncio.Event()
    delays: list[float] = []

    async def fake_get_redis(_settings):  # noqa: ANN001
        return fake_redis

    async def fake_ensure_group(redis, *, stream, group) -> None:  # noqa: ANN001, ARG001
        return None

    async def fake_claim(*args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("pending scan failed")

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)
        if len(delays) == 20:
            stop_event.set()

    monkeypatch.setattr(redis_queue, "_get_redis", fake_get_redis)
    monkeypatch.setattr(redis_queue, "_ensure_group", fake_ensure_group)
    monkeypatch.setattr(redis_queue, "_claim_stale_pending", fake_claim)
    monkeypatch.setattr(redis_queue.asyncio, "sleep", fake_sleep)

    redis_queue.asyncio.run(redis_queue._worker_loop(settings, stop_event))  # noqa: SLF001

    check(not not len(delays) == 20, "assertion failed")
    check(
        not not all((after >= before for before, after in zip(delays, delays[1:], strict=False))),
        "assertion failed",
    )
    check(not not delays[-1] == delays[-2], "assertion failed")
