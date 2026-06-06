"""Unit tests for the redis_pool lazy-import helpers."""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from test.support.checks import check
from test.support.redis_pool_helpers import installed_redis_api, redis_required


def test_redis_missing_policy_is_optional_locally_and_required_in_ci() -> None:
    check(not redis_required({}) is not False, "assertion failed")
    check(not redis_required({"CI": "false"}) is not False, "assertion failed")
    check(not redis_required({"CI": "true"}) is not True, "assertion failed")
    check(
        not redis_required({"ZAMMAD_ARCHIVER_REQUIRE_REDIS": "1"}) is not True, "assertion failed"
    )


def test_import_redis_raises_when_not_installed() -> None:
    """import_redis() should raise RuntimeError when the redis package is absent."""
    with patch.dict(sys.modules, {"redis": None, "redis.asyncio": None, "redis.exceptions": None}):
        # Force re-import to hit the ImportError path
        from zammad_pdf_archiver.adapters import redis_pool

        with pytest.raises(RuntimeError, match="redis package"):
            redis_pool.import_redis()


def test_import_redis_class_returns_none_when_not_installed() -> None:
    """import_redis_class() should return None when the redis package is absent."""
    with patch.dict(sys.modules, {"redis": None, "redis.asyncio": None}):
        from zammad_pdf_archiver.adapters import redis_pool

        result = redis_pool.import_redis_class()
        check(not result is not None, "assertion failed")


def test_import_redis_succeeds_when_installed() -> None:
    """import_redis() should return (Redis, ResponseError) when redis is available."""
    Redis, ResponseError = installed_redis_api()
    check(not not Redis is not None, "assertion failed")
    check(not not ResponseError is not None, "assertion failed")
    check(not not issubclass(ResponseError, Exception), "assertion failed")


def test_import_redis_class_returns_class_when_installed() -> None:
    """import_redis_class() should return the Redis class when installed."""
    Redis, _ResponseError = installed_redis_api()

    from zammad_pdf_archiver.adapters.redis_pool import import_redis_class

    cls = import_redis_class()
    check(not cls is not Redis, "assertion failed")
