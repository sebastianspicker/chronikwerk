"""Shared helpers for Redis pool tests."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

import pytest


class FakeCounter:
    def __init__(self) -> None:
        self.count = 0

    def inc(self) -> None:
        self.count += 1


def truthy_env(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def redis_required(env: Mapping[str, str]) -> bool:
    return truthy_env(env.get("CI")) or truthy_env(env.get("ZAMMAD_ARCHIVER_REQUIRE_REDIS"))


def installed_redis_api() -> tuple[Any, type[Exception]]:
    from zammad_pdf_archiver.adapters.redis_pool import import_redis

    try:
        Redis, ResponseError = import_redis()
    except RuntimeError as exc:
        if redis_required(os.environ):
            pytest.fail(
                "redis is required in CI/Redis verification but is not installed. "
                "Install with `pip install -e '.[redis]'` or run the Redis profile "
                "with project extras installed."
            )
        pytest.skip(str(exc))
    return Redis, ResponseError
