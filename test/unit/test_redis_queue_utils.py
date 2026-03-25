"""Unit tests for utility / helper functions in redis_queue module."""

from __future__ import annotations

import os
import socket

from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.jobs.redis_queue import (
    _as_str,
    _backend,
    _consumer_name,
    _merge_min_delay,
    _parse_float,
    _parse_int,
    _pending_count,
    _pending_entry_field,
    _retry_delay_seconds,
    _worker_key,
)

# ---------------------------------------------------------------------------
# _backend
# ---------------------------------------------------------------------------


class TestBackend:
    def test_returns_redis_queue(self, tmp_path) -> None:
        settings = make_settings(
            str(tmp_path),
            overrides={
                "workflow": {
                    "execution_backend": "redis_queue",
                    "redis_url": "redis://localhost/0",
                }
            },
        )
        assert _backend(settings) == "redis_queue"

    def test_returns_inprocess(self, tmp_path) -> None:
        settings = make_settings(
            str(tmp_path),
            overrides={"workflow": {"execution_backend": "inprocess"}},
        )
        assert _backend(settings) == "inprocess"

    def test_strips_and_lowercases(self, tmp_path) -> None:
        settings = make_settings(
            str(tmp_path),
            overrides={
                "workflow": {
                    "execution_backend": "  Redis_Queue  ",
                    "redis_url": "redis://localhost/0",
                }
            },
        )
        assert _backend(settings) == "redis_queue"


# ---------------------------------------------------------------------------
# _worker_key
# ---------------------------------------------------------------------------


class TestWorkerKey:
    def test_returns_pipe_delimited_string(self, tmp_path) -> None:
        settings = make_settings(
            str(tmp_path),
            overrides={
                "workflow": {
                    "execution_backend": "redis_queue",
                    "redis_url": "redis://localhost/0",
                    "queue_stream": "my:stream",
                    "queue_group": "my:group",
                }
            },
        )
        key = _worker_key(settings)
        assert key == "redis://localhost/0|my:stream|my:group"

    def test_empty_redis_url_is_blank(self, tmp_path) -> None:
        settings = make_settings(
            str(tmp_path),
            overrides={
                "workflow": {
                    "execution_backend": "inprocess",
                    "redis_url": None,
                    "queue_stream": "s",
                    "queue_group": "g",
                }
            },
        )
        key = _worker_key(settings)
        assert key == "|s|g"


# ---------------------------------------------------------------------------
# _consumer_name
# ---------------------------------------------------------------------------


class TestConsumerName:
    def test_returns_configured_name(self, tmp_path) -> None:
        settings = make_settings(
            str(tmp_path),
            overrides={"workflow": {"queue_consumer": "my-consumer"}},
        )
        assert _consumer_name(settings) == "my-consumer"

    def test_strips_whitespace_from_configured(self, tmp_path) -> None:
        settings = make_settings(
            str(tmp_path),
            overrides={"workflow": {"queue_consumer": "  padded  "}},
        )
        assert _consumer_name(settings) == "padded"

    def test_auto_generates_hostname_pid_when_none(self, tmp_path) -> None:
        settings = make_settings(
            str(tmp_path),
            overrides={"workflow": {"queue_consumer": None}},
        )
        name = _consumer_name(settings)
        assert name == f"{socket.gethostname()}-{os.getpid()}"

    def test_auto_generates_when_empty_string(self, tmp_path) -> None:
        settings = make_settings(
            str(tmp_path),
            overrides={"workflow": {"queue_consumer": "  "}},
        )
        name = _consumer_name(settings)
        expected = f"{socket.gethostname()}-{os.getpid()}"
        assert name == expected


# ---------------------------------------------------------------------------
# _as_str
# ---------------------------------------------------------------------------


class TestAsStr:
    def test_bytes_decoded(self) -> None:
        assert _as_str(b"hello") == "hello"

    def test_str_passthrough(self) -> None:
        assert _as_str("hello") == "hello"

    def test_int_converted(self) -> None:
        assert _as_str(42) == "42"

    def test_float_converted(self) -> None:
        assert _as_str(3.14) == "3.14"

    def test_none_converted(self) -> None:
        assert _as_str(None) == "None"

    def test_bytes_with_replacement_chars(self) -> None:
        raw = b"hello\xff\xfeworld"
        result = _as_str(raw)
        assert "hello" in result
        assert "world" in result


# ---------------------------------------------------------------------------
# _parse_float / _parse_int
# ---------------------------------------------------------------------------


class TestParseFloat:
    def test_valid_float_string(self) -> None:
        assert _parse_float("3.14") == 3.14

    def test_valid_int_string(self) -> None:
        assert _parse_float("7") == 7.0

    def test_valid_bytes(self) -> None:
        assert _parse_float(b"2.5") == 2.5

    def test_invalid_string_returns_default(self) -> None:
        assert _parse_float("not_a_number") == 0.0

    def test_invalid_string_returns_custom_default(self) -> None:
        assert _parse_float("bad", default=9.9) == 9.9

    def test_none_returns_default(self) -> None:
        assert _parse_float(None) == 0.0


class TestParseInt:
    def test_valid_int_string(self) -> None:
        assert _parse_int("42") == 42

    def test_valid_bytes(self) -> None:
        assert _parse_int(b"10") == 10

    def test_invalid_string_returns_default(self) -> None:
        assert _parse_int("nope") == 0

    def test_invalid_string_returns_custom_default(self) -> None:
        assert _parse_int("bad", default=99) == 99

    def test_none_returns_default(self) -> None:
        assert _parse_int(None) == 0

    def test_float_string_truncates(self) -> None:
        # int("3.14") raises, so should fall back to default
        assert _parse_int("3.14") == 0


# ---------------------------------------------------------------------------
# _retry_delay_seconds
# ---------------------------------------------------------------------------


class TestRetryDelaySeconds:
    def test_attempt_0(self, tmp_path) -> None:
        settings = make_settings(
            str(tmp_path),
            overrides={
                "workflow": {
                    "queue_retry_backoff_seconds": 2.0,
                }
            },
        )
        assert _retry_delay_seconds(settings, attempt=0) == 2.0  # 2.0 * 2^0 = 2.0

    def test_attempt_1(self, tmp_path) -> None:
        settings = make_settings(
            str(tmp_path),
            overrides={
                "workflow": {
                    "queue_retry_backoff_seconds": 2.0,
                }
            },
        )
        assert _retry_delay_seconds(settings, attempt=1) == 4.0  # 2.0 * 2^1 = 4.0

    def test_attempt_2(self, tmp_path) -> None:
        settings = make_settings(
            str(tmp_path),
            overrides={
                "workflow": {
                    "queue_retry_backoff_seconds": 2.0,
                }
            },
        )
        assert _retry_delay_seconds(settings, attempt=2) == 8.0  # 2.0 * 2^2 = 8.0

    def test_custom_base_delay(self, tmp_path) -> None:
        settings = make_settings(
            str(tmp_path),
            overrides={
                "workflow": {
                    "queue_retry_backoff_seconds": 5.0,
                }
            },
        )
        assert _retry_delay_seconds(settings, attempt=0) == 5.0
        assert _retry_delay_seconds(settings, attempt=1) == 10.0
        assert _retry_delay_seconds(settings, attempt=2) == 20.0

    def test_negative_attempt_clamped_to_zero(self, tmp_path) -> None:
        settings = make_settings(
            str(tmp_path),
            overrides={
                "workflow": {
                    "queue_retry_backoff_seconds": 3.0,
                }
            },
        )
        # max(0, -1) = 0, so 3.0 * 2^0 = 3.0
        assert _retry_delay_seconds(settings, attempt=-1) == 3.0


# ---------------------------------------------------------------------------
# _merge_min_delay
# ---------------------------------------------------------------------------


class TestMergeMinDelay:
    def test_none_and_none(self) -> None:
        assert _merge_min_delay(None, None) is None

    def test_none_and_positive(self) -> None:
        assert _merge_min_delay(None, 5.0) == 5.0

    def test_positive_and_none(self) -> None:
        assert _merge_min_delay(3.0, None) == 3.0

    def test_smaller_current(self) -> None:
        assert _merge_min_delay(3.0, 5.0) == 3.0

    def test_smaller_candidate(self) -> None:
        assert _merge_min_delay(5.0, 3.0) == 3.0

    def test_equal_values(self) -> None:
        assert _merge_min_delay(4.0, 4.0) == 4.0

    def test_zero_candidate_ignored(self) -> None:
        assert _merge_min_delay(3.0, 0.0) == 3.0

    def test_negative_candidate_ignored(self) -> None:
        assert _merge_min_delay(3.0, -1.0) == 3.0

    def test_none_current_and_zero_candidate(self) -> None:
        assert _merge_min_delay(None, 0.0) is None


# ---------------------------------------------------------------------------
# _pending_count
# ---------------------------------------------------------------------------


class TestPendingCount:
    def test_dict_with_pending(self) -> None:
        assert _pending_count({"pending": 5}) == 5

    def test_dict_with_pending_zero(self) -> None:
        assert _pending_count({"pending": 0}) == 0

    def test_object_with_pending_attr(self) -> None:
        class _FakePending:
            pending = 12

        assert _pending_count(_FakePending()) == 12

    def test_empty_dict(self) -> None:
        assert _pending_count({}) == 0

    def test_none(self) -> None:
        assert _pending_count(None) == 0

    def test_dict_with_non_int_pending(self) -> None:
        assert _pending_count({"pending": "not_int"}) == 0

    def test_object_with_non_int_pending(self) -> None:
        class _FakePending:
            pending = "not_int"

        assert _pending_count(_FakePending()) == 0


# ---------------------------------------------------------------------------
# _pending_entry_field
# ---------------------------------------------------------------------------


class TestPendingEntryField:
    def test_dict_entry(self) -> None:
        entry = {"message_id": "1-0", "consumer": "worker-a"}
        assert _pending_entry_field(entry, "message_id") == "1-0"
        assert _pending_entry_field(entry, "consumer") == "worker-a"

    def test_object_entry(self) -> None:
        class _FakeEntry:
            message_id = "2-0"
            consumer = "worker-b"

        entry = _FakeEntry()
        assert _pending_entry_field(entry, "message_id") == "2-0"
        assert _pending_entry_field(entry, "consumer") == "worker-b"

    def test_missing_key_in_dict(self) -> None:
        assert _pending_entry_field({}, "message_id") is None

    def test_missing_attr_on_object(self) -> None:
        class _Empty:
            pass

        assert _pending_entry_field(_Empty(), "message_id") is None
