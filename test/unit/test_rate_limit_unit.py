from __future__ import annotations

import asyncio

from zammad_pdf_archiver.app.middleware.rate_limit import _InMemoryTokenBucketLimiter


def test_rate_limit_returns_429_when_exhausted() -> None:
    """After exhausting the burst bucket (rps=1, burst=1), subsequent requests are denied."""

    async def _run() -> None:
        limiter = _InMemoryTokenBucketLimiter(rps=1.0, burst=1)

        # First request should be allowed (consumes the single burst token).
        assert await limiter.allow("client-a") is True

        # Second request immediately after should be denied (no tokens left,
        # no time elapsed for rps refill).
        assert await limiter.allow("client-a") is False

    asyncio.run(_run())
