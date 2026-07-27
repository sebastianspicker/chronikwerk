"""Supply common execution helpers for focused Zammad client tests."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from chronikwerk.adapters.zammad.client import (
    AsyncZammadClient,
    _RetryPolicy,
    _ZammadRuntimeOptions,
)
from chronikwerk.adapters.zammad.errors import ServerError


async def no_sleep(_: float) -> None:
    """Replace retry delays with a deterministic no-op."""
    return None


def _test_runtime(
    *,
    retry_policy: _RetryPolicy | None = None,
    http_client: httpx.AsyncClient | None = None,
    allow_private_networks: bool = True,
) -> _ZammadRuntimeOptions:
    """Build runtime configuration for Zammad client tests."""
    return _ZammadRuntimeOptions(
        retry_policy=retry_policy,
        sleep=no_sleep,
        http_client=http_client,
        allow_private_networks=allow_private_networks,
    )


def assert_create_internal_article_is_not_retried() -> None:
    """Exercise one failed article write against the one-attempt policy."""

    async def run() -> None:
        async with AsyncZammadClient(
            base_url="https://zammad.example",
            api_token="test-token",
            _runtime=_test_runtime(
                retry_policy=_RetryPolicy(max_retries=3, backoff_base_seconds=0.0)
            ),
        ) as client:
            with pytest.raises(ServerError, match="after 1 attempts"):
                await client.create_internal_article(123, "Subject", "<p>Body</p>")

    asyncio.run(run())
