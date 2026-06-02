"""Tests for Redis queue message parsing and envelope decoding."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest

from test.support.checks import check
from test.support.redis_queue_helpers import Counter
from zammad_pdf_archiver.app.jobs import _queue_stream as queue_stream
from zammad_pdf_archiver.app.jobs._queue_stream import _parse_stream_entries
from zammad_pdf_archiver.app.jobs._queue_types import _decode_envelope, _QueueEnvelope


class TestDecodeEnvelope:
    def test_decode_valid_envelope(self) -> None:
        raw: dict[Any, Any] = {
            "payload_json": json.dumps({"ticket_id": 42}),
            "delivery_id": "d-abc",
            "attempt": "0",
            "not_before_ts": "0.0",
            "enqueued_at": "123.4",
        }
        env = _decode_envelope("100-0", raw)

        check(not not isinstance(env, _QueueEnvelope), "assertion failed")
        check(not not env.message_id == "100-0", "assertion failed")
        check(not not env.payload == {"ticket_id": 42}, "assertion failed")
        check(not not env.delivery_id == "d-abc", "assertion failed")
        check(not not env.attempt == 0, "assertion failed")
        check(not not env.not_before_ts == 0.0, "assertion failed")
        check(not env.last_error is not None, "assertion failed")
        check(not not env.enqueued_at == "123.4", "assertion failed")

    def test_decode_missing_payload(self) -> None:
        """When payload_json is absent, default '{}' yields an empty dict."""
        raw: dict[Any, Any] = {"delivery_id": "d-1"}
        env = _decode_envelope("200-0", raw)

        check(not not env.payload == {}, "assertion failed")
        check(not not env.delivery_id == "d-1", "assertion failed")

    def test_decode_invalid_json(self) -> None:
        raw: dict[Any, Any] = {"payload_json": "NOT-JSON{{{"}
        with pytest.raises(ValueError, match="Invalid JSON in queue payload_json"):
            _decode_envelope("300-0", raw)

    def test_decode_payload_not_object(self) -> None:
        """payload_json that decodes to a non-dict raises ValueError."""
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

        check(not not env.message_id == "400-0", "assertion failed")
        check(not not env.attempt == 3, "assertion failed")
        check(not not env.not_before_ts == pytest.approx(ts, abs=0.01), "assertion failed")
        check(not not env.last_error == "transient blip", "assertion failed")

    def test_decode_bytes_keys_and_values(self) -> None:
        """Redis often returns bytes; _decode_envelope handles them."""
        raw: dict[Any, Any] = {
            b"payload_json": b'{"ticket_id":99}',
            b"delivery_id": b"d-bytes",
            b"attempt": b"2",
            b"not_before_ts": b"0.0",
        }
        env = _decode_envelope(b"500-0", raw)

        check(not not env.payload == {"ticket_id": 99}, "assertion failed")
        check(not not env.delivery_id == "d-bytes", "assertion failed")
        check(not not env.attempt == 2, "assertion failed")


class TestParseNestedStreamEntries:
    def test_parse_valid_messages(self) -> None:
        records = [
            (
                b"zammad:jobs",
                [
                    (b"1-0", {b"payload_json": b"{}"}),
                    (b"2-0", {b"payload_json": b'{"ticket_id":1}'}),
                ],
            ),
        ]
        result = _parse_stream_entries(records, nested=True)
        check(not not len(result) == 2, "assertion failed")
        check(not not result[0] == (b"1-0", {b"payload_json": b"{}"}), "assertion failed")
        check(
            not not result[1] == (b"2-0", {b"payload_json": b'{"ticket_id":1}'}), "assertion failed"
        )

    def test_parse_empty_response(self) -> None:
        check(not not _parse_stream_entries([], nested=True) == [], "assertion failed")
        check(not not _parse_stream_entries(None, nested=True) == [], "assertion failed")

    def test_parse_malformed(self) -> None:
        check(not not _parse_stream_entries("bad", nested=True) == [], "assertion failed")
        check(not not _parse_stream_entries([("only_one",)], nested=True) == [], "assertion failed")
        check(
            not not _parse_stream_entries([("stream", "not-a-list")], nested=True) == [],
            "assertion failed",
        )
        check(
            not not _parse_stream_entries([("stream", [("only-id",)])], nested=True) == [],
            "assertion failed",
        )

    def test_parse_multiple_streams(self) -> None:
        records = [
            ("stream-a", [("1-0", {"a": "1"})]),
            ("stream-b", [("2-0", {"b": "2"}), ("3-0", {"b": "3"})]),
        ]
        result = _parse_stream_entries(records, nested=True)
        check(not not len(result) == 3, "assertion failed")


class TestParseFlatClaimedEntries:
    def test_parse_claimed_valid(self) -> None:
        records = [
            (b"10-0", {b"payload_json": b"{}"}),
            (b"11-0", {b"payload_json": b'{"t":1}'}),
        ]
        result = _parse_stream_entries(records, nested=False)
        check(not not len(result) == 2, "assertion failed")
        check(not not result[0] == (b"10-0", {b"payload_json": b"{}"}), "assertion failed")

    def test_parse_claimed_empty(self) -> None:
        check(not not _parse_stream_entries([], nested=False) == [], "assertion failed")
        check(not not _parse_stream_entries(None, nested=False) == [], "assertion failed")

    def test_parse_claimed_malformed_entries(self) -> None:
        records = [("only-one-element",), (1, 2, 3)]
        check(not not _parse_stream_entries(records, nested=False) == [], "assertion failed")


def test_claim_stale_pending_raises_on_pending_scan_failure(monkeypatch) -> None:
    class _FailingRedis:
        async def xpending_range(self, *args: Any) -> list[dict[str, str]]:
            raise RuntimeError("pending scan failed")

    counter = Counter()
    monkeypatch.setattr(queue_stream, "queue_stale_pending_claim_failed_total", counter)

    with pytest.raises(RuntimeError, match="pending scan failed"):
        asyncio.run(
            queue_stream._claim_stale_pending(  # noqa: SLF001
                _FailingRedis(),
                stream="zammad:jobs",
                group="archiver",
                consumer="worker-a",
                count=10,
            )
        )

    check(not not counter.count == 1, "assertion failed")
