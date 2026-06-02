from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from unittest.mock import AsyncMock

import pytest

from test.support.checks import check
from test.support.redis_queue_helpers import FakeRedis, redis_queue_settings
from zammad_pdf_archiver.app.jobs import redis_queue
from zammad_pdf_archiver.app.jobs._queue_stream import _ack_and_delete, _push_dlq
from zammad_pdf_archiver.app.jobs._queue_types import _QueueEnvelope


class TestEnqueueTicketJob:
    def test_enqueue_basic(self, monkeypatch, tmp_path) -> None:
        settings = redis_queue_settings(tmp_path)
        fake = FakeRedis()

        async def _stub_get_redis(_s: Any) -> FakeRedis:
            return fake

        monkeypatch.setattr(redis_queue, "_get_redis", _stub_get_redis)

        msg_id = asyncio.run(
            redis_queue.enqueue_ticket_job(
                delivery_id="d-enq",
                payload={"ticket_id": 55},
                settings=settings,
            )
        )

        check(not not msg_id == "1-0", "assertion failed")
        check(not not len(fake.xadds) == 1, "assertion failed")
        stream, fields = fake.xadds[0]
        check(not not stream == "zammad:jobs", "assertion failed")
        check(not not json.loads(fields["payload_json"]) == {"ticket_id": 55}, "assertion failed")
        check(not not fields["delivery_id"] == "d-enq", "assertion failed")
        check(not not fields["attempt"] == "0", "assertion failed")
        check(not not float(fields["not_before_ts"]) == 0.0, "assertion failed")
        check(not "enqueued_at" not in fields, "assertion failed")
        check(not not "last_error" not in fields, "assertion failed")

    def test_enqueue_with_error(self, monkeypatch, tmp_path) -> None:
        settings = redis_queue_settings(tmp_path)
        fake = FakeRedis()

        async def _stub_get_redis(_s: Any) -> FakeRedis:
            return fake

        monkeypatch.setattr(redis_queue, "_get_redis", _stub_get_redis)

        asyncio.run(
            redis_queue.enqueue_ticket_job(
                delivery_id="d-err",
                payload={"ticket_id": 1},
                settings=settings,
                last_error="timeout connecting",
            )
        )

        _, fields = fake.xadds[0]
        check(not not fields["last_error"] == "timeout connecting", "assertion failed")

    def test_enqueue_with_not_before(self, monkeypatch, tmp_path) -> None:
        settings = redis_queue_settings(tmp_path)
        fake = FakeRedis()

        async def _stub_get_redis(_s: Any) -> FakeRedis:
            return fake

        monkeypatch.setattr(redis_queue, "_get_redis", _stub_get_redis)

        future_ts = time.time() + 60.0
        asyncio.run(
            redis_queue.enqueue_ticket_job(
                delivery_id="d-nb",
                payload={"ticket_id": 2},
                settings=settings,
                attempt=2,
                not_before_ts=future_ts,
            )
        )

        _, fields = fake.xadds[0]
        check(
            not not float(fields["not_before_ts"]) == pytest.approx(future_ts, abs=0.01),
            "assertion failed",
        )
        check(not not fields["attempt"] == "2", "assertion failed")

    def test_enqueue_truncates_long_error(self, monkeypatch, tmp_path) -> None:
        settings = redis_queue_settings(tmp_path)
        fake = FakeRedis()

        async def _stub_get_redis(_s: Any) -> FakeRedis:
            return fake

        monkeypatch.setattr(redis_queue, "_get_redis", _stub_get_redis)

        long_error = "x" * 1000
        asyncio.run(
            redis_queue.enqueue_ticket_job(
                delivery_id="d-long",
                payload={},
                settings=settings,
                last_error=long_error,
            )
        )

        _, fields = fake.xadds[0]
        check(not not len(fields["last_error"]) == 500, "assertion failed")


class TestAckAndDelete:
    def test_ack_and_delete(self) -> None:
        fake = FakeRedis()

        asyncio.run(
            _ack_and_delete(
                fake,
                stream="zammad:jobs",
                group="archiver",
                message_id="77-0",
            )
        )

        check(not not fake.acked == [("zammad:jobs", "archiver", "77-0")], "assertion failed")
        check(not not fake.deleted == [("zammad:jobs", "77-0")], "assertion failed")

    def test_ack_and_delete_order(self) -> None:
        """xdel runs after a successful xack."""
        calls: list[str] = []

        async def _xack(*_a: Any) -> int:
            calls.append("ack")
            return 1

        async def _xdel(*_a: Any) -> int:
            calls.append("del")
            return 1

        mock_redis = AsyncMock()
        mock_redis.xack = _xack
        mock_redis.xdel = _xdel

        asyncio.run(
            _ack_and_delete(
                mock_redis,
                stream="s",
                group="g",
                message_id="1-0",
            )
        )

        check(not not calls == ["ack", "del"], "assertion failed")

    def test_ack_and_delete_xdel_does_not_run_after_xack_failure(self) -> None:
        """xack failure must leave the stream entry for recovery."""
        fake = FakeRedis()

        async def _failing_xack(*args: Any) -> int:  # noqa: ARG001
            raise RuntimeError("ack failed")

        fake.xack = _failing_xack  # type: ignore[assignment]

        with pytest.raises(RuntimeError, match="ack failed"):
            asyncio.run(
                _ack_and_delete(
                    fake,
                    stream="s",
                    group="g",
                    message_id="1-0",
                )
            )

        check(not not fake.deleted == [], "assertion failed")


class TestPushDlq:
    def test_push_dlq(self, tmp_path) -> None:
        settings = redis_queue_settings(tmp_path)
        fake = FakeRedis()

        envelope = _QueueEnvelope(
            message_id="99-0",
            payload={"ticket_id": 42},
            delivery_id="d-dlq",
            attempt=5,
            not_before_ts=0.0,
            last_error="some error",
            enqueued_at="123.4",
        )

        asyncio.run(
            _push_dlq(
                fake,
                settings=settings,
                envelope=envelope,
                reason="retry_exhausted",
                error_message="still failing",
            )
        )

        check(not not len(fake.xadds) == 1, "assertion failed")
        stream, fields = fake.xadds[0]
        check(not not stream == "zammad:jobs:dlq", "assertion failed")
        check(not not json.loads(fields["payload_json"]) == {"ticket_id": 42}, "assertion failed")
        check(not not fields["delivery_id"] == "d-dlq", "assertion failed")
        check(not not fields["attempt"] == "5", "assertion failed")
        check(not not fields["reason"] == "retry_exhausted", "assertion failed")
        check(not not fields["error"] == "still failing", "assertion failed")
        check(not not fields["enqueued_at"] == "123.4", "assertion failed")
        check(not "failed_at" not in fields, "assertion failed")

    def test_push_dlq_without_error_message(self, tmp_path) -> None:
        settings = redis_queue_settings(tmp_path)
        fake = FakeRedis()

        envelope = _QueueEnvelope(
            message_id="101-0",
            payload={},
            delivery_id=None,
            attempt=0,
            not_before_ts=0.0,
            last_error=None,
        )

        asyncio.run(
            _push_dlq(
                fake,
                settings=settings,
                envelope=envelope,
                reason="permanent_error",
            )
        )

        _, fields = fake.xadds[0]
        check(not not "error" not in fields, "assertion failed")
        check(not not fields["delivery_id"] == "", "assertion failed")
