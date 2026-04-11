"""Tests for stream reading, message processing, queue stats, and public API in redis_queue."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.jobs import redis_queue

# ---------------------------------------------------------------------------
# Shared fake Redis
# ---------------------------------------------------------------------------

@dataclass
class _FakeRedis:
    """Minimal fake Redis supporting xreadgroup, xadd, xack, xdel, xlen, xpending."""

    xadds: list[tuple[str, dict[str, str]]] = field(default_factory=list)
    acked: list[tuple[str, str, str]] = field(default_factory=list)
    deleted: list[tuple[str, str]] = field(default_factory=list)
    stream_lengths: dict[str, int] = field(default_factory=dict)
    pending_count: int = 0

    # Pre-programmed responses for xreadgroup keyed on the stream id ("0" or ">")
    xreadgroup_responses: dict[str, Any] = field(default_factory=dict)

    # xgroup_create tracking
    groups_created: list[tuple[str, str]] = field(default_factory=list)

    async def xreadgroup(
        self,
        groupname: str,  # noqa: ARG002
        consumername: str,  # noqa: ARG002
        streams: dict[str, str],
        count: int = 10,  # noqa: ARG002
        block: int | None = None,  # noqa: ARG002
    ) -> Any:
        stream_id = next(iter(streams.values()))
        return self.xreadgroup_responses.get(stream_id)

    async def xadd(self, stream: str, fields: dict[str, str], **kwargs: Any) -> str:  # noqa: ARG002
        self.xadds.append((stream, fields))
        self.stream_lengths[stream] = self.stream_lengths.get(stream, 0) + 1
        return f"{len(self.xadds)}-0"

    async def xack(self, stream: str, group: str, message_id: str) -> int:
        self.acked.append((stream, group, message_id))
        return 1

    async def xdel(self, stream: str, message_id: str) -> int:
        self.deleted.append((stream, message_id))
        return 1

    async def xlen(self, stream: str) -> int:
        return self.stream_lengths.get(stream, 0)

    async def xpending(self, stream: str, group: str) -> dict[str, int]:  # noqa: ARG002
        return {"pending": self.pending_count}

    async def xgroup_create(
        self, stream: str, group: str, id: str = "0", mkstream: bool = True  # noqa: A002, ARG002
    ) -> None:
        self.groups_created.append((stream, group))

    async def aclose(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_redis_settings(tmp_path: Any, **extra: Any) -> Any:
    overrides: dict[str, Any] = {
        "workflow": {
            "execution_backend": "redis_queue",
            "redis_url": "redis://localhost/0",
        }
    }
    overrides["workflow"].update(extra)
    return make_settings(str(tmp_path), overrides=overrides)


def _valid_raw_fields(ticket_id: int = 42, attempt: int = 0) -> dict[str, str]:
    return {
        "payload_json": json.dumps({"ticket_id": ticket_id}),
        "delivery_id": "d-test",
        "attempt": str(attempt),
        "not_before_ts": "0.0",
    }


# ===========================================================================
# 1. _read_own_pending
# ===========================================================================


class TestReadOwnPending:
    def test_read_own_pending_has_messages(self) -> None:
        messages_data = [("msg-1", _valid_raw_fields()), ("msg-2", _valid_raw_fields(99))]
        fake = _FakeRedis(xreadgroup_responses={"0": [["mystream", messages_data]]})

        result = asyncio.run(
            redis_queue._read_own_pending(  # noqa: SLF001
                fake, stream="mystream", group="grp", consumer="c1", count=10
            )
        )
        assert result == messages_data

    def test_read_own_pending_empty(self) -> None:
        fake = _FakeRedis(xreadgroup_responses={"0": []})

        result = asyncio.run(
            redis_queue._read_own_pending(  # noqa: SLF001
                fake, stream="mystream", group="grp", consumer="c1", count=10
            )
        )
        assert result == []


# ===========================================================================
# 2. _read_new_messages
# ===========================================================================


class TestReadNewMessages:
    def test_read_new_messages_has_messages(self) -> None:
        messages_data = [("new-1", _valid_raw_fields())]
        fake = _FakeRedis(xreadgroup_responses={">": [["mystream", messages_data]]})

        result = asyncio.run(
            redis_queue._read_new_messages(  # noqa: SLF001
                fake,
                stream="mystream",
                group="grp",
                consumer="c1",
                count=10,
                block_ms=500,
            )
        )
        assert result == messages_data

    def test_read_new_messages_timeout(self) -> None:
        # xreadgroup returns None on timeout (no messages available)
        fake = _FakeRedis(xreadgroup_responses={">": None})

        result = asyncio.run(
            redis_queue._read_new_messages(  # noqa: SLF001
                fake,
                stream="mystream",
                group="grp",
                consumer="c1",
                count=10,
                block_ms=500,
            )
        )
        assert result == []


# ===========================================================================
# 3. _process_messages
# ===========================================================================


class TestProcessMessages:
    def test_process_messages_success(self, monkeypatch, tmp_path) -> None:
        settings = _make_redis_settings(tmp_path)
        fake = _FakeRedis()

        async def _stub_handle(redis, *, settings, envelope):  # noqa: ANN001, ARG001
            return 0.0

        monkeypatch.setattr(redis_queue, "_handle_envelope", _stub_handle)

        messages = [("m-1", _valid_raw_fields())]
        result = asyncio.run(
            redis_queue._process_messages(fake, settings=settings, messages=messages)  # noqa: SLF001
        )
        # 0.0 delay merges to None (since _merge_min_delay treats <=0 as no delay)
        assert result is None

    def test_process_messages_decode_error(self, monkeypatch, tmp_path) -> None:
        settings = _make_redis_settings(tmp_path)
        fake = _FakeRedis()

        # Stub record_history_event so it doesn't try to connect to real Redis
        monkeypatch.setattr(
            redis_queue, "record_history_event", AsyncMock(return_value=True)
        )

        # Malformed message: payload_json is not valid JSON
        malformed_fields: dict[str, str] = {"payload_json": "<<<not json>>>"}
        messages = [("bad-1", malformed_fields)]

        result = asyncio.run(
            redis_queue._process_messages(fake, settings=settings, messages=messages)  # noqa: SLF001
        )

        # Should have pushed to DLQ
        assert any(
            stream == settings.workflow.queue_dlq_stream for stream, _ in fake.xadds
        )
        # Should have acked and deleted the bad message
        assert ("bad-1",) == tuple(mid for _, _, mid in fake.acked)
        assert any(mid == "bad-1" for _, mid in fake.deleted)
        # No pending delay from decode errors
        assert result is None

    def test_process_messages_returns_min_delay(self, monkeypatch, tmp_path) -> None:
        settings = _make_redis_settings(tmp_path)
        fake = _FakeRedis()

        call_count = 0

        async def _stub_handle(redis, *, settings, envelope):  # noqa: ANN001, ARG001
            nonlocal call_count
            call_count += 1
            # First message has a 5s delay, second has 2s delay
            return 5.0 if call_count == 1 else 2.0

        monkeypatch.setattr(redis_queue, "_handle_envelope", _stub_handle)

        messages = [
            ("m-1", _valid_raw_fields(1)),
            ("m-2", _valid_raw_fields(2)),
        ]
        result = asyncio.run(
            redis_queue._process_messages(fake, settings=settings, messages=messages)  # noqa: SLF001
        )
        # min(5.0, 2.0) == 2.0
        assert result == 2.0


# ===========================================================================
# 4. get_queue_stats
# ===========================================================================


class TestGetQueueStats:
    def test_queue_stats_redis_backend(self, monkeypatch, tmp_path) -> None:
        settings = _make_redis_settings(tmp_path)
        fake = _FakeRedis(
            stream_lengths={"zammad:jobs": 7, "zammad:jobs:dlq": 2},
            pending_count=3,
        )

        async def _stub_get_redis(_settings: Any) -> _FakeRedis:
            return fake

        monkeypatch.setattr(redis_queue, "_get_redis", _stub_get_redis)

        stats = asyncio.run(redis_queue.get_queue_stats(settings))

        assert stats["execution_backend"] == "redis_queue"
        assert stats["queue_enabled"] is True
        assert stats["queue_depth"] == 7
        assert stats["dlq_depth"] == 2
        assert stats["pending"] == 3
        assert "consumer" in stats
        assert "stream" in stats

    def test_queue_stats_inprocess_backend(self, tmp_path) -> None:
        settings = make_settings(
            str(tmp_path),
            overrides={"workflow": {"execution_backend": "inprocess"}},
        )
        stats = asyncio.run(redis_queue.get_queue_stats(settings))

        assert stats == {"execution_backend": "inprocess", "queue_enabled": False}


# ===========================================================================
# 5. RedisQueueManager
# ===========================================================================


class TestRedisQueueManager:
    def test_start_worker_creates_task(self, monkeypatch, tmp_path) -> None:
        settings = _make_redis_settings(tmp_path)

        sentinel_task = MagicMock(spec=asyncio.Task)
        sentinel_task.done.return_value = False

        async def _run() -> asyncio.Task[None] | None:
            mgr = redis_queue.RedisQueueManager()

            async def _fake_worker_loop(s: Any, e: Any) -> None:  # noqa: ARG001
                try:
                    await asyncio.sleep(100)
                except asyncio.CancelledError:
                    return

            monkeypatch.setattr(redis_queue, "_worker_loop", _fake_worker_loop)
            task = await mgr.start_worker(settings)
            assert task is not None
            assert isinstance(task, asyncio.Task)
            # Cleanup
            await mgr.stop_all(timeout=0.1)
            return task

        asyncio.run(_run())

    def test_stop_worker_signals_stop(self, monkeypatch, tmp_path) -> None:
        settings = _make_redis_settings(tmp_path)

        async def _run() -> None:
            mgr = redis_queue.RedisQueueManager()
            stop_observed = False

            async def _fake_worker_loop(s: Any, stop_event: asyncio.Event) -> None:  # noqa: ARG001
                nonlocal stop_observed
                await stop_event.wait()
                stop_observed = True

            monkeypatch.setattr(redis_queue, "_worker_loop", _fake_worker_loop)
            await mgr.start_worker(settings)
            await mgr.stop_worker(settings, timeout=2.0)
            assert stop_observed

        asyncio.run(_run())

    def test_start_worker_reuses_existing(self, monkeypatch, tmp_path) -> None:
        settings = _make_redis_settings(tmp_path)

        async def _run() -> None:
            mgr = redis_queue.RedisQueueManager()

            async def _fake_worker_loop(s: Any, e: Any) -> None:  # noqa: ARG001
                try:
                    await asyncio.sleep(100)
                except asyncio.CancelledError:
                    return

            monkeypatch.setattr(redis_queue, "_worker_loop", _fake_worker_loop)
            task1 = await mgr.start_worker(settings)
            task2 = await mgr.start_worker(settings)
            assert task1 is task2
            await mgr.stop_all(timeout=0.1)

        asyncio.run(_run())


# ===========================================================================
# 6. Public API wrappers
# ===========================================================================


class TestPublicAPIWrappers:
    def test_start_queue_worker_delegates(self, monkeypatch, tmp_path) -> None:
        settings = _make_redis_settings(tmp_path)
        calls: list[Any] = []

        async def _fake_start(self: Any, s: Any) -> None:  # noqa: ARG001
            calls.append(s)
            return None

        monkeypatch.setattr(redis_queue.RedisQueueManager, "start_worker", _fake_start)

        asyncio.run(redis_queue.start_queue_worker(settings))
        assert len(calls) == 1
        assert calls[0] is settings

    def test_stop_queue_worker_delegates(self, monkeypatch, tmp_path) -> None:
        settings = _make_redis_settings(tmp_path)
        calls: list[Any] = []

        async def _fake_stop(self: Any, s: Any, *, timeout: float = 3.0) -> None:  # noqa: ARG001
            calls.append((s, timeout))

        monkeypatch.setattr(redis_queue.RedisQueueManager, "stop_worker", _fake_stop)

        asyncio.run(redis_queue.stop_queue_worker(settings, timeout=5.0))
        assert len(calls) == 1
        assert calls[0] == (settings, 5.0)

    def test_aclose_queue_clients_delegates(self, monkeypatch) -> None:
        calls: list[bool] = []

        async def _fake_close_all() -> None:
            calls.append(True)

        monkeypatch.setattr(
            "zammad_pdf_archiver.adapters.redis_pool.close_all", _fake_close_all
        )

        asyncio.run(redis_queue.aclose_queue_clients())
        assert calls == [True]
