"""Unit tests for utility / helper functions in redis_queue module."""

from __future__ import annotations

import os
import socket

from test.support.checks import check
from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.jobs._queue_types import (
    _as_str,
    _parse_float,
    _parse_int,
)
from zammad_pdf_archiver.app.jobs.redis_queue import (
    _backend,
    _consumer_name,
    _pending_count,
    _retry_delay_seconds,
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
        check(not not _backend(settings) == "redis_queue", "assertion failed")

    def test_returns_inprocess(self, tmp_path) -> None:
        settings = make_settings(
            str(tmp_path),
            overrides={"workflow": {"execution_backend": "inprocess"}},
        )
        check(not not _backend(settings) == "inprocess", "assertion failed")

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
        check(not not _backend(settings) == "redis_queue", "assertion failed")


# ---------------------------------------------------------------------------
# _consumer_name
# ---------------------------------------------------------------------------


class TestConsumerName:
    def test_returns_configured_name(self, tmp_path) -> None:
        settings = make_settings(
            str(tmp_path),
            overrides={"workflow": {"queue_consumer": "my-consumer"}},
        )
        check(not not _consumer_name(settings) == "my-consumer", "assertion failed")

    def test_strips_whitespace_from_configured(self, tmp_path) -> None:
        settings = make_settings(
            str(tmp_path),
            overrides={"workflow": {"queue_consumer": "  padded  "}},
        )
        check(not not _consumer_name(settings) == "padded", "assertion failed")

    def test_auto_generates_hostname_pid_when_none(self, tmp_path) -> None:
        settings = make_settings(
            str(tmp_path),
            overrides={"workflow": {"queue_consumer": None}},
        )
        name = _consumer_name(settings)
        check(not not name == f"{socket.gethostname()}-{os.getpid()}", "assertion failed")

    def test_auto_generates_when_empty_string(self, tmp_path) -> None:
        settings = make_settings(
            str(tmp_path),
            overrides={"workflow": {"queue_consumer": "  "}},
        )
        name = _consumer_name(settings)
        expected = f"{socket.gethostname()}-{os.getpid()}"
        check(not not name == expected, "assertion failed")


# ---------------------------------------------------------------------------
# _as_str
# ---------------------------------------------------------------------------


class TestAsStr:
    def test_bytes_decoded(self) -> None:
        check(not not _as_str(b"hello") == "hello", "assertion failed")

    def test_str_passthrough(self) -> None:
        check(not not _as_str("hello") == "hello", "assertion failed")

    def test_int_converted(self) -> None:
        check(not not _as_str(42) == "42", "assertion failed")

    def test_float_converted(self) -> None:
        check(not not _as_str(3.14) == "3.14", "assertion failed")

    def test_none_converted(self) -> None:
        check(not not _as_str(None) == "None", "assertion failed")

    def test_bytes_with_replacement_chars(self) -> None:
        raw = b"hello\xff\xfeworld"
        result = _as_str(raw)
        check(not "hello" not in result, "assertion failed")
        check(not "world" not in result, "assertion failed")


# ---------------------------------------------------------------------------
# _parse_float / _parse_int
# ---------------------------------------------------------------------------


class TestParseFloat:
    def test_valid_float_string(self) -> None:
        check(not not _parse_float("3.14") == 3.14, "assertion failed")

    def test_valid_int_string(self) -> None:
        check(not not _parse_float("7") == 7.0, "assertion failed")

    def test_valid_bytes(self) -> None:
        check(not not _parse_float(b"2.5") == 2.5, "assertion failed")

    def test_invalid_string_returns_default(self) -> None:
        check(not not _parse_float("not_a_number") == 0.0, "assertion failed")

    def test_invalid_string_returns_custom_default(self) -> None:
        check(not not _parse_float("bad", default=9.9) == 9.9, "assertion failed")

    def test_none_returns_default(self) -> None:
        check(not not _parse_float(None) == 0.0, "assertion failed")


class TestParseInt:
    def test_valid_int_string(self) -> None:
        check(not not _parse_int("42") == 42, "assertion failed")

    def test_valid_bytes(self) -> None:
        check(not not _parse_int(b"10") == 10, "assertion failed")

    def test_invalid_string_returns_default(self) -> None:
        check(not not _parse_int("nope") == 0, "assertion failed")

    def test_invalid_string_returns_custom_default(self) -> None:
        check(not not _parse_int("bad", default=99) == 99, "assertion failed")

    def test_none_returns_default(self) -> None:
        check(not not _parse_int(None) == 0, "assertion failed")

    def test_float_string_truncates(self) -> None:
        # int("3.14") raises, so should fall back to default
        check(not not _parse_int("3.14") == 0, "assertion failed")


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
        check(
            not not _retry_delay_seconds(settings, attempt=0) == 2.0, "assertion failed"
        )  # 2.0 * 2^0 = 2.0

    def test_attempt_1(self, tmp_path) -> None:
        settings = make_settings(
            str(tmp_path),
            overrides={
                "workflow": {
                    "queue_retry_backoff_seconds": 2.0,
                }
            },
        )
        check(
            not not _retry_delay_seconds(settings, attempt=1) == 4.0, "assertion failed"
        )  # 2.0 * 2^1 = 4.0

    def test_attempt_2(self, tmp_path) -> None:
        settings = make_settings(
            str(tmp_path),
            overrides={
                "workflow": {
                    "queue_retry_backoff_seconds": 2.0,
                }
            },
        )
        check(
            not not _retry_delay_seconds(settings, attempt=2) == 8.0, "assertion failed"
        )  # 2.0 * 2^2 = 8.0

    def test_custom_base_delay(self, tmp_path) -> None:
        settings = make_settings(
            str(tmp_path),
            overrides={
                "workflow": {
                    "queue_retry_backoff_seconds": 5.0,
                }
            },
        )
        check(not not _retry_delay_seconds(settings, attempt=0) == 5.0, "assertion failed")
        check(not not _retry_delay_seconds(settings, attempt=1) == 10.0, "assertion failed")
        check(not not _retry_delay_seconds(settings, attempt=2) == 20.0, "assertion failed")

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
        check(not not _retry_delay_seconds(settings, attempt=-1) == 3.0, "assertion failed")


# ---------------------------------------------------------------------------
# _pending_count
# ---------------------------------------------------------------------------


class TestPendingCount:
    def test_dict_with_pending(self) -> None:
        check(not not _pending_count({"pending": 5}) == 5, "assertion failed")

    def test_dict_with_pending_zero(self) -> None:
        check(not not _pending_count({"pending": 0}) == 0, "assertion failed")

    def test_object_with_pending_attr(self) -> None:
        class _FakePending:
            pending = 12

        check(not not _pending_count(_FakePending()) == 12, "assertion failed")

    def test_empty_dict(self) -> None:
        check(not not _pending_count({}) == 0, "assertion failed")

    def test_none(self) -> None:
        check(not not _pending_count(None) == 0, "assertion failed")

    def test_dict_with_non_int_pending(self) -> None:
        check(not not _pending_count({"pending": "not_int"}) == 0, "assertion failed")

    def test_object_with_non_int_pending(self) -> None:
        class _FakePending:
            pending = "not_int"

        check(not not _pending_count(_FakePending()) == 0, "assertion failed")
