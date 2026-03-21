"""Tests for redis_queue message parsing, envelope helpers, and enqueue/DLQ functions."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock

import pytest

from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.jobs import redis_queue
from zammad_pdf_archiver.app.jobs.redis_queue import (
    _decode_envelope,
    _extract_claimed_messages,
    _extract_stream_messages,
    _QueueEnvelope,
)

# ---------------------------------------------------------------------------
# Reusable helpers
# ---------------------------------------------------------------------------

def _settings(tmp_path: Any) -> redis_queue.Settings:
    return make_settings(
        str(tmp_path),
        overrides={
            "workflow": {
                "execution_backend": "redis_queue",
                "redis_url": "redis://localhost/0",
                "queue_stream": "zammad:jobs",
                "queue_group": "archiver",
                "queue_dlq_stream": "zammad:jobs:dlq",
                "queue_retry_max_attempts": 3,
                "queue_retry_backoff_seconds": 1.0,
            }
        },
    )


@dataclass
class _FakeRedis:
    """Minimal async Redis stub used across envelope / enqueue tests."""

    xadds: list[tuple[str, dict[str, str]]] = field(default_factory=list)
    acked: list[tuple[str, str, str]] = field(default_factory=list)
    deleted: list[tuple[str, str]] = field(default_factory=list)
    dlq_entries: list[tuple[str, dict[str, str]]] = field(default_factory=list)

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        self.xadds.append((stream, fields))
        if stream.endswith(":dlq"):
            self.dlq_entries.append((f"{len(self.dlq_entries) + 1}-0", fields))
        return f"{len(self.xadds)}-0"

    async def xack(self, stream: str, group: str, message_id: str) -> int:
        self.acked.append((stream, group, message_id))
        return 1

    async def xdel(self, stream: str, message_id: str) -> int:
        self.deleted.append((stream, message_id))
        return 1

    async def xrange(
        self,
        stream: str,  # noqa: ARG002
        min: str,  # noqa: A002, ARG002
        max: str,  # noqa: A002, ARG002
        count: int,
    ) -> list[tuple[str, dict[str, str]]]:
        return self.dlq_entries[:count]


# ===================================================================
# 1. _decode_envelope
# ===================================================================


class TestDecodeEnvelope:
    def test_decode_valid_envelope(self) -> None:
        raw: dict[Any, Any] = {
            "payload_json": json.dumps({"ticket_id": 42}),
            "delivery_id": "d-abc",
            "attempt": "0",
            "not_before_ts": "0.0",
        }
        env = _decode_envelope("100-0", raw)

        assert isinstance(env, _QueueEnvelope)
        assert env.message_id == "100-0"
        assert env.payload == {"ticket_id": 42}
        assert env.delivery_id == "d-abc"
        assert env.attempt == 0
        assert env.not_before_ts == 0.0
        assert env.last_error is None

    def test_decode_missing_payload(self) -> None:
        """When payload_json is absent, default '{}' yields an empty dict."""
        raw: dict[Any, Any] = {"delivery_id": "d-1"}
        env = _decode_envelope("200-0", raw)

        assert env.payload == {}
        assert env.delivery_id == "d-1"

    def test_decode_invalid_json(self) -> None:
        raw: dict[Any, Any] = {"payload_json": "NOT-JSON{{{"}
        with pytest.raises(json.JSONDecodeError):
            _decode_envelope("300-0", raw)

    def test_decode_payload_not_object(self) -> None:
        """payload_json that decodes to a non-dict (e.g. a list) raises ValueError."""
        raw: dict[Any, Any] = {"payload_json": "[1,2,3]"}
        with pytest.raises(ValueError, match="not an object"):
            _decode_envelope("301-0", raw)

    def test_decode_with_attempt_and_timestamps(self) -> None:
        ts = time.time() + 30.0
        raw: dict[Any, Any] = {
            "payload_json": json.dumps({"ticket_id": 7}),
            "delivery_id": "d-ts",
            "attempt": "3",
            "not_before_ts": str(ts),
            "enqueued_at": str(time.time()),
            "last_error": "transient blip",
        }
        env = _decode_envelope(b"400-0", raw)

        assert env.message_id == "400-0"
        assert env.attempt == 3
        assert env.not_before_ts == pytest.approx(ts, abs=0.01)
        assert env.last_error == "transient blip"

    def test_decode_bytes_keys_and_values(self) -> None:
        """Redis often returns bytes — _decode_envelope handles them transparently."""
        raw: dict[Any, Any] = {
            b"payload_json": b'{"ticket_id":99}',
            b"delivery_id": b"d-bytes",
            b"attempt": b"2",
            b"not_before_ts": b"0.0",
        }
        env = _decode_envelope(b"500-0", raw)

        assert env.payload == {"ticket_id": 99}
        assert env.delivery_id == "d-bytes"
        assert env.attempt == 2


# ===================================================================
# 2. _extract_stream_messages
# ===================================================================


class TestExtractStreamMessages:
    def test_extract_valid_messages(self) -> None:
        records = [
            (b"zammad:jobs", [
                (b"1-0", {b"payload_json": b"{}"}),
                (b"2-0", {b"payload_json": b'{"ticket_id":1}'}),
            ]),
        ]
        result = _extract_stream_messages(records)
        assert len(result) == 2
        assert result[0] == (b"1-0", {b"payload_json": b"{}"})
        assert result[1] == (b"2-0", {b"payload_json": b'{"ticket_id":1}'})

    def test_extract_empty_response(self) -> None:
        assert _extract_stream_messages([]) == []
        assert _extract_stream_messages(None) == []

    def test_extract_malformed(self) -> None:
        # Not a list at all
        assert _extract_stream_messages("bad") == []
        # Inner record is not a 2-tuple
        assert _extract_stream_messages([("only_one",)]) == []
        # Messages portion is not a list
        assert _extract_stream_messages([("stream", "not-a-list")]) == []
        # Message entries inside are wrong length
        assert _extract_stream_messages([("stream", [("only-id",)])]) == []

    def test_extract_multiple_streams(self) -> None:
        records = [
            ("stream-a", [("1-0", {"a": "1"})]),
            ("stream-b", [("2-0", {"b": "2"}), ("3-0", {"b": "3"})]),
        ]
        result = _extract_stream_messages(records)
        assert len(result) == 3


# ===================================================================
# 3. _extract_claimed_messages
# ===================================================================


class TestExtractClaimedMessages:
    def test_extract_claimed_valid(self) -> None:
        records = [
            (b"10-0", {b"payload_json": b"{}"}),
            (b"11-0", {b"payload_json": b'{"t":1}'}),
        ]
        result = _extract_claimed_messages(records)
        assert len(result) == 2
        assert result[0] == (b"10-0", {b"payload_json": b"{}"})

    def test_extract_claimed_empty(self) -> None:
        assert _extract_claimed_messages([]) == []
        assert _extract_claimed_messages(None) == []

    def test_extract_claimed_malformed_entries(self) -> None:
        records = [("only-one-element",), (1, 2, 3)]
        assert _extract_claimed_messages(records) == []


# ===================================================================
# 4. enqueue_ticket_job
# ===================================================================


class TestEnqueueTicketJob:
    def test_enqueue_basic(self, monkeypatch, tmp_path) -> None:
        settings = _settings(tmp_path)
        fake = _FakeRedis()

        async def _stub_get_redis(_s: Any) -> _FakeRedis:
            return fake

        monkeypatch.setattr(redis_queue, "_get_redis", _stub_get_redis)

        msg_id = asyncio.run(
            redis_queue.enqueue_ticket_job(
                delivery_id="d-enq",
                payload={"ticket_id": 55},
                settings=settings,
            )
        )

        assert msg_id == "1-0"
        assert len(fake.xadds) == 1
        stream, fields = fake.xadds[0]
        assert stream == "zammad:jobs"
        assert json.loads(fields["payload_json"]) == {"ticket_id": 55}
        assert fields["delivery_id"] == "d-enq"
        assert fields["attempt"] == "0"
        assert float(fields["not_before_ts"]) == 0.0
        assert "enqueued_at" in fields
        assert "last_error" not in fields

    def test_enqueue_with_error(self, monkeypatch, tmp_path) -> None:
        settings = _settings(tmp_path)
        fake = _FakeRedis()

        async def _stub_get_redis(_s: Any) -> _FakeRedis:
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
        assert fields["last_error"] == "timeout connecting"

    def test_enqueue_with_not_before(self, monkeypatch, tmp_path) -> None:
        settings = _settings(tmp_path)
        fake = _FakeRedis()

        async def _stub_get_redis(_s: Any) -> _FakeRedis:
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
        assert float(fields["not_before_ts"]) == pytest.approx(future_ts, abs=0.01)
        assert fields["attempt"] == "2"

    def test_enqueue_truncates_long_error(self, monkeypatch, tmp_path) -> None:
        settings = _settings(tmp_path)
        fake = _FakeRedis()

        async def _stub_get_redis(_s: Any) -> _FakeRedis:
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
        assert len(fields["last_error"]) == 500


# ===================================================================
# 5. _ack_and_delete
# ===================================================================


class TestAckAndDelete:
    def test_ack_and_delete(self) -> None:
        fake = _FakeRedis()

        asyncio.run(
            redis_queue._ack_and_delete(
                fake,
                stream="zammad:jobs",
                group="archiver",
                message_id="77-0",
            )
        )

        assert fake.acked == [("zammad:jobs", "archiver", "77-0")]
        assert fake.deleted == [("zammad:jobs", "77-0")]

    def test_ack_and_delete_order(self) -> None:
        """xdel is always called even if xack raises — and xack runs first."""
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
            redis_queue._ack_and_delete(
                mock_redis,
                stream="s",
                group="g",
                message_id="1-0",
            )
        )

        assert calls == ["ack", "del"]

    def test_ack_and_delete_xdel_runs_after_xack_failure(self) -> None:
        """xdel must execute even when xack raises an exception."""
        fake = _FakeRedis()

        async def _failing_xack(*args: Any) -> int:  # noqa: ARG001
            raise RuntimeError("ack failed")

        fake.xack = _failing_xack  # type: ignore[assignment]

        with pytest.raises(RuntimeError, match="ack failed"):
            asyncio.run(
                redis_queue._ack_and_delete(
                    fake,
                    stream="s",
                    group="g",
                    message_id="1-0",
                )
            )

        # xdel still called via the finally block
        assert fake.deleted == [("s", "1-0")]


# ===================================================================
# 6. _push_dlq
# ===================================================================


class TestPushDlq:
    def test_push_dlq(self, tmp_path) -> None:
        settings = _settings(tmp_path)
        fake = _FakeRedis()

        envelope = _QueueEnvelope(
            message_id="99-0",
            payload={"ticket_id": 42},
            delivery_id="d-dlq",
            attempt=5,
            not_before_ts=0.0,
            last_error="some error",
        )

        asyncio.run(
            redis_queue._push_dlq(
                fake,
                settings=settings,
                envelope=envelope,
                reason="retry_exhausted",
                error_message="still failing",
            )
        )

        assert len(fake.xadds) == 1
        stream, fields = fake.xadds[0]
        assert stream == "zammad:jobs:dlq"
        assert json.loads(fields["payload_json"]) == {"ticket_id": 42}
        assert fields["delivery_id"] == "d-dlq"
        assert fields["attempt"] == "5"
        assert fields["reason"] == "retry_exhausted"
        assert fields["error"] == "still failing"
        assert "failed_at" in fields

    def test_push_dlq_without_error_message(self, tmp_path) -> None:
        settings = _settings(tmp_path)
        fake = _FakeRedis()

        envelope = _QueueEnvelope(
            message_id="101-0",
            payload={},
            delivery_id=None,
            attempt=0,
            not_before_ts=0.0,
            last_error=None,
        )

        asyncio.run(
            redis_queue._push_dlq(
                fake,
                settings=settings,
                envelope=envelope,
                reason="permanent_error",
            )
        )

        _, fields = fake.xadds[0]
        assert "error" not in fields
        assert fields["delivery_id"] == ""


# ===================================================================
# 7. replay_dlq
# ===================================================================


class TestReplayDlq:
    def test_replay_dlq(self, monkeypatch, tmp_path) -> None:
        settings = _settings(tmp_path)
        fake = _FakeRedis(
            dlq_entries=[
                (
                    "1-0",
                    {
                        "payload_json": json.dumps({"ticket_id": 10}),
                        "delivery_id": "d-replay-1",
                        "attempt": "4",
                        "reason": "retry_exhausted",
                    },
                ),
                (
                    "2-0",
                    {
                        "payload_json": json.dumps({"ticket_id": 20}),
                        "delivery_id": "",
                        "attempt": "2",
                        "reason": "permanent_error",
                    },
                ),
            ]
        )

        enqueue_calls: list[dict[str, Any]] = []

        async def _tracking_enqueue(**kwargs: Any) -> str:
            enqueue_calls.append(kwargs)
            return await fake.xadd(settings.workflow.queue_stream, {"payload_json": "{}"})

        async def _stub_get_redis(_s: Any) -> _FakeRedis:
            return fake

        monkeypatch.setattr(redis_queue, "_get_redis", _stub_get_redis)
        monkeypatch.setattr(redis_queue, "enqueue_ticket_job", _tracking_enqueue)

        replayed = asyncio.run(
            redis_queue.replay_dlq(settings, limit=10)
        )

        assert replayed == 2

        # Both entries should be re-enqueued with attempt=0
        assert len(enqueue_calls) == 2
        assert enqueue_calls[0]["attempt"] == 0
        assert enqueue_calls[0]["payload"] == {"ticket_id": 10}
        assert enqueue_calls[0]["delivery_id"] == "d-replay-1"
        assert enqueue_calls[1]["attempt"] == 0
        assert enqueue_calls[1]["payload"] == {"ticket_id": 20}
        assert enqueue_calls[1]["delivery_id"] is None  # empty string → None

        # DLQ entries should be deleted after replay
        assert ("zammad:jobs:dlq", "1-0") in fake.deleted
        assert ("zammad:jobs:dlq", "2-0") in fake.deleted

    def test_replay_dlq_zero_limit(self, monkeypatch, tmp_path) -> None:
        settings = _settings(tmp_path)

        replayed = asyncio.run(
            redis_queue.replay_dlq(settings, limit=0)
        )
        assert replayed == 0

    def test_replay_dlq_skips_invalid_payload(self, monkeypatch, tmp_path) -> None:
        settings = _settings(tmp_path)
        fake = _FakeRedis(
            dlq_entries=[
                ("1-0", {"payload_json": "NOT-JSON", "delivery_id": "d-bad"}),
                ("2-0", {"payload_json": json.dumps({"ticket_id": 7}), "delivery_id": "d-ok"}),
            ]
        )

        enqueue_calls: list[dict[str, Any]] = []

        async def _tracking_enqueue(**kwargs: Any) -> str:
            enqueue_calls.append(kwargs)
            return await fake.xadd(settings.workflow.queue_stream, {"payload_json": "{}"})

        async def _stub_get_redis(_s: Any) -> _FakeRedis:
            return fake

        monkeypatch.setattr(redis_queue, "_get_redis", _stub_get_redis)
        monkeypatch.setattr(redis_queue, "enqueue_ticket_job", _tracking_enqueue)

        replayed = asyncio.run(
            redis_queue.replay_dlq(settings, limit=10)
        )

        # Only the valid entry should be replayed
        assert replayed == 1
        assert enqueue_calls[0]["payload"] == {"ticket_id": 7}
