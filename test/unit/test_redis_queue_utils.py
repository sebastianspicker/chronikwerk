"""Unit tests for utility / helper functions in redis_queue module."""

from __future__ import annotations

import os
import socket

from test.support.checks import check
from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.jobs.redis_queue import (
    _backend,
    _consumer_name,
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
