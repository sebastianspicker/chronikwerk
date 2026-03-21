"""Unit tests for the redis_pool lazy-import helpers."""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest


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
        assert result is None


def test_import_redis_succeeds_when_installed() -> None:
    """import_redis() should return (Redis, ResponseError) when redis is available."""
    try:
        import redis  # noqa: F401
    except ImportError:
        pytest.skip("redis package not installed")

    from zammad_pdf_archiver.adapters.redis_pool import import_redis

    Redis, ResponseError = import_redis()
    assert Redis is not None
    assert ResponseError is not None


def test_import_redis_class_returns_class_when_installed() -> None:
    """import_redis_class() should return the Redis class when installed."""
    try:
        import redis  # noqa: F401
    except ImportError:
        pytest.skip("redis package not installed")

    from zammad_pdf_archiver.adapters.redis_pool import import_redis_class

    cls = import_redis_class()
    assert cls is not None
