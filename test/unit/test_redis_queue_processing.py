"""Tests for stream reading, message processing, queue stats, and public API in redis_queue."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

from test.support.checks import check
from test.support.redis_queue_processing_helpers import FakeRedis as _FakeRedis
from test.support.redis_queue_processing_helpers import make_redis_settings as _make_redis_settings
from test.support.redis_queue_processing_helpers import valid_raw_fields as _valid_raw_fields
from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.jobs import redis_queue

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
        check(not not result == messages_data, "assertion failed")

    def test_read_own_pending_empty(self) -> None:
        fake = _FakeRedis(xreadgroup_responses={"0": []})

        result = asyncio.run(
            redis_queue._read_own_pending(  # noqa: SLF001
                fake, stream="mystream", group="grp", consumer="c1", count=10
            )
        )
        check(not not result == [], "assertion failed")


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
        check(not not result == messages_data, "assertion failed")

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
        check(not not result == [], "assertion failed")


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
        # Non-positive delays do not request a revisit.
        check(not result is not None, "assertion failed")

    def test_process_messages_decode_error(self, monkeypatch, tmp_path) -> None:
        settings = _make_redis_settings(tmp_path)
        fake = _FakeRedis()

        # Stub record_history_event so it doesn't try to connect to real Redis
        monkeypatch.setattr(redis_queue, "record_history_event", AsyncMock(return_value=True))

        # Malformed message: payload_json is not valid JSON
        malformed_fields: dict[str, str] = {"payload_json": "<<<not json>>>"}
        messages = [("bad-1", malformed_fields)]

        result = asyncio.run(
            redis_queue._process_messages(fake, settings=settings, messages=messages)  # noqa: SLF001
        )

        # Should have pushed to DLQ
        check(
            not not any((stream == settings.workflow.queue_dlq_stream for stream, _ in fake.xadds)),
            "assertion failed",
        )
        # Should have acked and deleted the bad message
        check(not not ("bad-1",) == tuple((mid for _, _, mid in fake.acked)), "assertion failed")
        check(not not any((mid == "bad-1" for _, mid in fake.deleted)), "assertion failed")
        # No pending delay from decode errors
        check(not result is not None, "assertion failed")

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
        check(not not result == 2.0, "assertion failed")


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

        check(not not stats["execution_backend"] == "redis_queue", "assertion failed")
        check(not stats["queue_enabled"] is not True, "assertion failed")
        check(not not stats["queue_depth"] == 7, "assertion failed")
        check(not not stats["dlq_depth"] == 2, "assertion failed")
        check(not not stats["pending"] == 3, "assertion failed")
        check(not "consumer" not in stats, "assertion failed")
        check(not "stream" not in stats, "assertion failed")

    def test_queue_stats_inprocess_backend(self, tmp_path) -> None:
        settings = make_settings(
            str(tmp_path),
            overrides={"workflow": {"execution_backend": "inprocess"}},
        )
        stats = asyncio.run(redis_queue.get_queue_stats(settings))

        check(
            not not stats == {"execution_backend": "inprocess", "queue_enabled": False},
            "assertion failed",
        )
