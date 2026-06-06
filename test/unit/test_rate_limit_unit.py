from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route
from starlette.testclient import TestClient

from test.support.checks import check
from test.support.credentials import fake_credential
from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.middleware.rate_limit import (
    RateLimitMiddleware,
    _client_key,
    _client_key_from_header,
    _client_key_from_scope,
    _InMemoryTokenBucketLimiter,
)


def test_rate_limit_returns_429_when_exhausted() -> None:
    """After exhausting the burst bucket (rps=1, burst=1), subsequent requests are denied."""

    async def _run() -> None:
        limiter = _InMemoryTokenBucketLimiter(rps=1.0, burst=1)

        # First request should be allowed (consumes the single burst token).
        check(not await limiter.allow("client-a") is not True, "assertion failed")

        # Second request immediately after should be denied (no tokens left,
        # no time elapsed for rps refill).
        check(not await limiter.allow("client-a") is not False, "assertion failed")

    asyncio.run(_run())


def test_rate_limit_eviction_when_max_entries_exceeded() -> None:
    """When max_entries is exceeded, old entries are evicted."""

    async def _run() -> None:
        limiter = _InMemoryTokenBucketLimiter(rps=10.0, burst=10, max_entries=5)

        # Fill beyond max_entries to trigger eviction.
        for i in range(7):
            await limiter.allow(f"client-{i}")

        # Limiter should still work correctly after eviction.
        check(not await limiter.allow("new-client") is not True, "assertion failed")
        check(not not len(limiter._buckets) <= 5, "assertion failed")  # noqa: SLF001

    asyncio.run(_run())


def test_client_key_from_scope_with_valid_host() -> None:
    scope: dict[str, Any] = {"client": ("192.168.1.1", 12345)}
    check(not not _client_key_from_scope(scope) == "192.168.1.1", "assertion failed")


def test_client_key_from_scope_missing_client() -> None:
    scope: dict[str, Any] = {}
    check(not not _client_key_from_scope(scope) == "unknown", "assertion failed")


def test_client_key_from_scope_empty_client() -> None:
    scope: dict[str, Any] = {"client": []}
    check(not not _client_key_from_scope(scope) == "unknown", "assertion failed")


def test_client_key_from_header_extracts_first_forwarded_for() -> None:
    scope: dict[str, Any] = {
        "headers": [(b"x-forwarded-for", b"10.0.0.1, 10.0.0.2")],
        "client": ("192.168.1.1", 0),
    }
    result = _client_key_from_header(scope, "x-forwarded-for")
    check(not not result == "10.0.0.1", "assertion failed")


def test_client_key_from_header_falls_back_to_scope_when_missing() -> None:
    scope: dict[str, Any] = {"headers": [], "client": ("127.0.0.1", 0)}
    result = _client_key_from_header(scope, "x-forwarded-for")
    check(not not result == "127.0.0.1", "assertion failed")


def test_client_key_uses_scope_when_no_header_name() -> None:
    scope: dict[str, Any] = {"client": ("10.1.2.3", 0)}
    check(not not _client_key(scope, None) == "10.1.2.3", "assertion failed")


def test_client_key_uses_header_when_provided() -> None:
    scope: dict[str, Any] = {
        "headers": [(b"x-real-ip", b"172.16.0.5")],
        "client": ("10.0.0.1", 0),
    }
    result = _client_key(scope, "x-real-ip")
    check(not not result == "172.16.0.5", "assertion failed")


def test_rate_limit_middleware_settings_none_passes_through() -> None:
    """With settings=None the middleware is disabled and passes all requests through."""

    async def _run() -> None:
        downstream = AsyncMock()
        mw = RateLimitMiddleware(downstream, settings=None)

        scope: dict[str, Any] = {"type": "http", "path": "/ingest/webhook"}
        await mw(scope, AsyncMock(), AsyncMock())

        downstream.assert_called_once()

    asyncio.run(_run())


def test_rate_limit_middleware_non_http_passes_through() -> None:
    """Non-HTTP scopes (websocket, lifespan) are not rate-limited."""

    async def _run() -> None:
        downstream = AsyncMock()
        settings = _make_settings_with_rate_limit(enabled=True, rps=1, burst=1)
        mw = RateLimitMiddleware(downstream, settings=settings)

        scope: dict[str, Any] = {"type": "websocket", "path": "/ingest/webhook"}
        await mw(scope, AsyncMock(), AsyncMock())
        downstream.assert_called_once()

    asyncio.run(_run())


def test_rate_limit_middleware_returns_429_on_exhaustion() -> None:
    """The middleware itself returns 429 when rate exceeded."""

    async def _run() -> None:
        downstream = AsyncMock()
        settings = _make_settings_with_rate_limit(enabled=True, rps=0.0, burst=1)
        mw = RateLimitMiddleware(downstream, settings=settings)

        scope: dict[str, Any] = {
            "type": "http",
            "path": "/ingest",
            "headers": [],
            "client": ("1.2.3.4", 0),
        }

        # First passes, second is blocked.
        await mw(scope, AsyncMock(), AsyncMock())
        check(not not downstream.call_count == 1, "assertion failed")

        # Second call — bucket is empty with rps=0 (no refill).
        responses: list[int] = []

        async def _send(msg: Any) -> None:
            if msg.get("type") == "http.response.start":
                responses.append(msg["status"])

        await mw(scope, AsyncMock(), _send)
        check(
            not not downstream.call_count == 1, "assertion failed"
        )  # downstream was NOT called again
        check(not 429 not in responses, "assertion failed")

    asyncio.run(_run())


# ===================================================================
# Helpers
# ===================================================================


def _make_settings_with_rate_limit(*, enabled: bool, rps: float, burst: int) -> Any:
    from zammad_pdf_archiver.config.settings import Settings

    return Settings.from_mapping(
        {
            "zammad": {"base_url": "https://z.test", "api_token": fake_credential("tok")},
            "storage": {"root": "/var/lib/test-ratelimit"},
            "hardening": {
                "webhook": {"allow_unsigned": bool(1), "allow_unsigned_when_no_secret": bool(1)},
                "rate_limit": {"enabled": enabled, "rps": rps, "burst": burst},
            },
        }
    )


async def _ingest_endpoint(request: Request) -> Response:
    return Response("ok", status_code=200)


def _rate_limited_test_client(tmp_path):
    starlette_app = Starlette(routes=[Route("/ingest", _ingest_endpoint, methods=["POST"])])
    settings = make_settings(
        str(tmp_path),
        overrides={"hardening": {"rate_limit": {"enabled": True, "rps": 1.0, "burst": 1}}},
    )
    middleware = RateLimitMiddleware(starlette_app, settings=settings)
    return TestClient(middleware, raise_server_exceptions=False), middleware


# ---------------------------------------------------------------------------
# New tests for missing coverage
# ---------------------------------------------------------------------------


def test_bucket_eviction_when_max_entries_exceeded() -> None:
    async def _run() -> None:
        limiter = _InMemoryTokenBucketLimiter(rps=1.0, burst=100, max_entries=5)
        # Fill well past max_entries to trigger eviction
        for i in range(20):
            await limiter.allow(f"client-{i}")
        # Eviction should have occurred; limiter still works
        check(not await limiter.allow("new-client") is not True, "assertion failed")
        check(not not len(limiter._buckets) <= 5, "assertion failed")  # noqa: SLF001

    asyncio.run(_run())


def test_client_key_from_scope_unknown_when_no_client() -> None:
    from zammad_pdf_archiver.app.middleware.rate_limit import _client_key_from_scope

    scope = {"type": "http", "path": "/", "headers": []}
    check(not not _client_key_from_scope(scope) == "unknown", "assertion failed")


def test_client_key_from_header_comma_split() -> None:
    from zammad_pdf_archiver.app.middleware.rate_limit import _client_key_from_header

    scope = {
        "type": "http",
        "path": "/",
        "headers": [(b"x-forwarded-for", b"1.2.3.4, 5.6.7.8")],
        "client": ["9.9.9.9", 12345],
    }
    check(
        not not _client_key_from_header(scope, "x-forwarded-for") == "1.2.3.4", "assertion failed"
    )


def test_client_key_from_header_whitespace_falls_back_to_scope() -> None:
    from zammad_pdf_archiver.app.middleware.rate_limit import _client_key_from_header

    scope = {
        "type": "http",
        "path": "/",
        "headers": [(b"x-forwarded-for", b"   ")],
        "client": ["9.9.9.9", 12345],
    }
    # Whitespace-only header value → break → fall back to connection client
    check(
        not not _client_key_from_header(scope, "x-forwarded-for") == "9.9.9.9", "assertion failed"
    )


def test_rate_limit_middleware_returns_429_when_exhausted(tmp_path) -> None:
    client, _middleware = _rate_limited_test_client(tmp_path)
    r1 = client.post("/ingest")
    check(not not r1.status_code == 200, "assertion failed")
    r2 = client.post("/ingest")
    check(not not r2.status_code == 429, "assertion failed")


def test_rate_limit_middleware_none_limiter_passes_through(tmp_path) -> None:
    """When _limiter is None traffic passes through to the app."""
    client, middleware = _rate_limited_test_client(tmp_path)
    middleware._limiter = None  # noqa: SLF001

    r = client.post("/ingest")
    check(not not r.status_code == 200, "assertion failed")
