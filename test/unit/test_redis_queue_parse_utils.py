"""Unit tests for Redis queue parsing helpers."""

from __future__ import annotations

from test.support.checks import check
from zammad_pdf_archiver.app.jobs._queue_types import _as_str, _parse_float, _parse_int
from zammad_pdf_archiver.app.jobs.redis_queue import _pending_count


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
        check(not not _parse_int("3.14") == 0, "assertion failed")


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
