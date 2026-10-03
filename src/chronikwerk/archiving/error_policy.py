"""Classify archive failures as transient or permanent and format safe failure messages."""

from __future__ import annotations

import errno

import httpx

from chronikwerk.failures import PermanentError, TransientError, wrap_exception

_HTTP_TIMEOUT = "HTTP timeout"
_HTTP_REQUEST_ERROR = "HTTP connection/request error"
_FS_GENERIC_ERROR = "Filesystem error"

_TRANSIENT_ERRNOS: set[int] = {
    # Temporary / retryable.
    errno.EAGAIN,
    getattr(errno, "EWOULDBLOCK", errno.EAGAIN),
    errno.ETIMEDOUT,
    # Common network share / remote FS flakiness.
    errno.ECONNRESET,
    errno.EPIPE,
    getattr(errno, "ENOTCONN", 107),
    getattr(errno, "ESTALE", 116),
    errno.EIO,
    # Infrastructure/outage style issues that can resolve without changing inputs.
    getattr(errno, "ENETDOWN", 100),
    getattr(errno, "ENETUNREACH", 101),
    getattr(errno, "EHOSTUNREACH", 113),
    # Environment can be fixed by ops (mount, capacity).
    errno.ENOENT,
    errno.ENOSPC,
    getattr(errno, "EDQUOT", 122),
    getattr(errno, "EROFS", 30),
}

_PERMANENT_ERRNOS: set[int] = {
    errno.EACCES,
    errno.EPERM,
    errno.EINVAL,
    errno.ENAMETOOLONG,
    errno.ENOTDIR,
    errno.EISDIR,
}

_TRANSIENT_EXCEPTION_RULES: tuple[tuple[type[BaseException], str], ...] = (
    (httpx.TimeoutException, _HTTP_TIMEOUT),
    (httpx.RequestError, _HTTP_REQUEST_ERROR),
)


def _format_http_error(status: int, *, is_auth: bool = False) -> str:
    """Format an HTTP failure without exposing sensitive response content."""
    if is_auth:
        return f"HTTP {status} (auth/permission) from upstream"
    return f"HTTP {status} from upstream"


def _format_fs_error(err: int, *, is_temporary: bool) -> str:
    """Format a filesystem failure without leaking host-specific details."""
    if is_temporary:
        return f"Temporary filesystem error (errno={err})"
    return f"Filesystem policy/permission error (errno={err})"


def _classify_http_status(exc: httpx.HTTPStatusError) -> TransientError | PermanentError:
    status = exc.response.status_code
    if 500 <= status <= 599:
        return TransientError(_format_http_error(status))
    if status in (401, 403):
        return PermanentError(_format_http_error(status, is_auth=True))
    return PermanentError(_format_http_error(status))


def _classify_os_error(exc: OSError) -> TransientError | PermanentError:
    err = exc.errno
    if isinstance(err, int) and err in _TRANSIENT_ERRNOS:
        return TransientError(_format_fs_error(err, is_temporary=True))
    if isinstance(err, int) and err in _PERMANENT_ERRNOS:
        return PermanentError(_format_fs_error(err, is_temporary=False))

    # Unknown OS errors default to permanent to avoid endless reprocessing loops.
    return PermanentError(_FS_GENERIC_ERROR)


def _classify_httpx_error(exc: BaseException) -> TransientError | PermanentError | None:
    if isinstance(exc, httpx.HTTPStatusError):
        return _classify_http_status(exc)
    for exc_type, message in _TRANSIENT_EXCEPTION_RULES:
        if isinstance(exc, exc_type):
            return TransientError(message)
    return None


def classify(exc: BaseException) -> TransientError | PermanentError:
    """
    Classify an exception into retryable (TransientError) vs non-retryable (PermanentError).

    Always returns a classification; unknown exceptions are wrapped as permanent.

    Policy goals:
      - Predictable ticket state transitions (avoid accidental infinite retry loops).
      - Keep retryable failures retryable: network timeouts, upstream 5xx, rate limits,
        and certain filesystem errors commonly seen with network shares.
    """
    if isinstance(exc, TransientError | PermanentError):
        return exc

    httpx_result = _classify_httpx_error(exc)
    if httpx_result is not None:
        return httpx_result

    # Filesystem issues (local or network share).
    if isinstance(exc, OSError):
        return _classify_os_error(exc)

    # Validation/data issues (e.g. missing required ticket fields, path policy violations).
    if isinstance(exc, ValueError | TypeError):
        return PermanentError(str(exc) or exc.__class__.__name__)

    # Fail-safe default: stop automatic reprocessing unless explicitly classified transient.
    return wrap_exception(exc)
