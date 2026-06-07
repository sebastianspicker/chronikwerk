from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import monotonic

from starlette.types import ASGIApp, Receive, Scope, Send

from zammad_pdf_archiver.app.constants import INGEST_PROTECTED_PATHS
from zammad_pdf_archiver.app.middleware import rate_limit_keys
from zammad_pdf_archiver.app.protected_paths import normalized_protected_path
from zammad_pdf_archiver.app.responses import api_error
from zammad_pdf_archiver.config.settings import Settings

_METRICS_PATH = "/metrics"
_client_key = rate_limit_keys.client_key
_client_key_from_header = rate_limit_keys.client_key_from_header
_client_key_from_scope = rate_limit_keys.client_key_from_scope


@dataclass
class _Bucket:
    tokens: float
    updated_at: float


class _InMemoryTokenBucketLimiter:
    def __init__(
        self,
        *,
        rps: float,
        burst: int,
        max_entries: int = 10_000,
        now=monotonic,
    ) -> None:
        self._rps = float(rps)
        self._burst = float(burst)
        self._max_entries = int(max_entries)
        self._now = now
        self._lock = asyncio.Lock()
        self._buckets: dict[str, _Bucket] = {}

    async def allow(self, key: str) -> bool:
        """Return True if the request for key is within the rate limit, False otherwise."""
        now = float(self._now())
        async with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                if len(self._buckets) >= self._max_entries:
                    to_evict_count = len(self._buckets) - self._max_entries + 1
                    for evicted_key in list(self._buckets)[:to_evict_count]:
                        self._buckets.pop(evicted_key, None)
                bucket = _Bucket(tokens=self._burst, updated_at=now)
                self._buckets[key] = bucket

            elapsed = max(0.0, now - bucket.updated_at)
            if self._rps > 0:
                bucket.tokens = min(self._burst, bucket.tokens + elapsed * self._rps)
            bucket.updated_at = now

            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return True

            return False


def _rate_limited():
    return api_error(429, "rate_limited", code="rate_limited")


class RateLimitMiddleware:
    def __init__(self, app: ASGIApp, *, settings: Settings | None) -> None:
        self.app = app

        if settings is None:
            self._enabled = False
            self._paths: frozenset[str] = frozenset()
            self._limiter: _InMemoryTokenBucketLimiter | None = None
            return

        config = settings.hardening.rate_limit
        self._enabled = bool(config.enabled)
        self._paths = frozenset(
            set(INGEST_PROTECTED_PATHS) | ({_METRICS_PATH} if config.include_metrics else set())
        )
        self._client_key_header: str | None = config.client_key_header or None
        self._limiter = _InMemoryTokenBucketLimiter(rps=config.rps, burst=config.burst)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if not self._enabled or normalized_protected_path(scope.get("path")) not in self._paths:
            await self.app(scope, receive, send)
            return

        limiter = self._limiter
        if limiter is None:
            await self.app(scope, receive, send)
            return

        key = _client_key(scope, self._client_key_header)
        if not await limiter.allow(key):
            await _rate_limited()(scope, receive, send)
            return

        await self.app(scope, receive, send)
