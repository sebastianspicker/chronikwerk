"""Locks retry classification for transport, Zammad, and unexpected failures."""

from __future__ import annotations

import errno

import httpx
import pytest

from chronikwerk.adapters.zammad.errors import (
    AuthError,
    ClientError,
    NotFoundError,
    RateLimitError,
    ServerError,
)
from chronikwerk.app.jobs.retry_policy import classify
from chronikwerk.domain.errors import PermanentError, TransientError


def _http_status_error(status_code: int) -> httpx.HTTPStatusError:
    """Construct an HTTP status failure without a network request."""
    req = httpx.Request("GET", "https://example.invalid/")
    resp = httpx.Response(status_code, request=req)
    return httpx.HTTPStatusError("status error", request=req, response=resp)


@pytest.mark.parametrize(
    ("exc", "expected_type"),
    [
        (httpx.ReadTimeout("timeout"), TransientError),
        (httpx.ConnectError("connect"), TransientError),
        (_http_status_error(503), TransientError),
        (_http_status_error(401), PermanentError),
        (ServerError("zammad 5xx"), TransientError),
        (RateLimitError("zammad 429"), TransientError),
        (AuthError("zammad auth"), PermanentError),
        (NotFoundError("zammad 404"), PermanentError),
        (ClientError("zammad 400"), PermanentError),
        (OSError(errno.EAGAIN, "try again"), TransientError),
        (OSError(errno.EACCES, "nope"), PermanentError),
        (ValueError("bad input"), PermanentError),
        (TypeError("bad type"), PermanentError),
        (Exception("unknown"), PermanentError),
    ],
)
def test_classify_table(exc: BaseException, expected_type: type[Exception]) -> None:
    """Keep retry classification stable across transport and domain failures."""
    out = classify(exc)
    assert isinstance(out, expected_type)


def test_classify_returns_same_transient_instance() -> None:
    exc = TransientError("t")
    assert classify(exc) is exc


def test_classify_returns_same_permanent_instance() -> None:
    exc = PermanentError("p")
    assert classify(exc) is exc
