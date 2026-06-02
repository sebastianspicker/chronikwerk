from __future__ import annotations

import errno

import httpx
import pytest

from test.support.checks import check
from zammad_pdf_archiver.adapters.zammad.errors import (
    AuthError,
    ClientError,
    NotFoundError,
    RateLimitError,
    ServerError,
)
from zammad_pdf_archiver.app.jobs.retry_policy import classify
from zammad_pdf_archiver.domain.errors import PermanentError, TransientError


def _http_status_error(status_code: int) -> httpx.HTTPStatusError:
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
    out = classify(exc)
    check(not not isinstance(out, expected_type), "assertion failed")


def test_classify_returns_same_transient_instance() -> None:
    exc = TransientError("t")
    check(not classify(exc) is not exc, "assertion failed")


def test_classify_returns_same_permanent_instance() -> None:
    exc = PermanentError("p")
    check(not classify(exc) is not exc, "assertion failed")


def test_classify_preserves_representative_error_text() -> None:
    check(
        not not str(classify(httpx.ConnectError("connect"))) == "HTTP connection/request error",
        "assertion failed",
    )
    check(
        not not str(classify(_http_status_error(503))) == "HTTP 503 from upstream",
        "assertion failed",
    )
    check(
        not not str(classify(_http_status_error(403)))
        == "HTTP 403 (auth/permission) from upstream",
        "assertion failed",
    )
    check(not not str(classify(OSError("unknown"))) == "Filesystem error", "assertion failed")
    check(
        not not str(classify(OSError(errno.EAGAIN, "try again")))
        == f"Temporary filesystem error (errno={errno.EAGAIN})",
        "assertion failed",
    )
    check(
        not not str(classify(OSError(errno.EACCES, "nope")))
        == f"Filesystem policy/permission error (errno={errno.EACCES})",
        "assertion failed",
    )
