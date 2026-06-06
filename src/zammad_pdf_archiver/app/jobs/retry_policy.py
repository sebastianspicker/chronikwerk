from __future__ import annotations

import errno

import httpx

from zammad_pdf_archiver.app.jobs.retry_policy_zammad import (
    classify_zammad_exception,
)
from zammad_pdf_archiver.domain.errors import PermanentError, TransientError, wrap_exception

_HTTP_TIMEOUT = "HTTP timeout"
_HTTP_REQUEST_ERROR = "HTTP connection/request error"
_HTTP_UPSTREAM_ERROR = "HTTP {status} from upstream"
_HTTP_AUTH_ERROR = "HTTP {status} (auth/permission) from upstream"

_FS_TEMPORARY_ERROR = "Temporary filesystem error (errno={errno})"
_FS_POLICY_ERROR = "Filesystem policy/permission error (errno={errno})"
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


def _format_http_error(status: int | None, *, is_auth: bool = False) -> str:
    if status is None:
        return _HTTP_REQUEST_ERROR

    if is_auth:
        return _HTTP_AUTH_ERROR.format(status=status)

    return _HTTP_UPSTREAM_ERROR.format(status=status)


def _format_fs_error(error_number: int | None, *, is_temporary: bool = False) -> str:
    if error_number is None:
        return _FS_GENERIC_ERROR

    if is_temporary:
        return _FS_TEMPORARY_ERROR.format(errno=error_number)

    return _FS_POLICY_ERROR.format(errno=error_number)


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


def classify(exc: BaseException) -> TransientError | PermanentError:
    """
    Classify an exception into retryable (TransientError) vs non-retryable (PermanentError).

    Policy goals:
      - Predictable ticket state transitions (avoid accidental infinite retry loops).
      - Keep retryable failures retryable: network timeouts, upstream 5xx, rate limits,
        and certain filesystem errors commonly seen with network shares.
    """
    if isinstance(exc, (TransientError, PermanentError)):
        return exc

    classified = _classify_http_exception(exc)
    if classified is not None:
        return classified

    classified = classify_zammad_exception(exc)
    if classified is not None:
        return classified

    classified = _classify_local_exception(exc)
    if classified is not None:
        return classified

    # Fail-safe default: stop automatic reprocessing unless explicitly classified transient.
    return wrap_exception(exc)


def _classify_http_exception(exc: BaseException) -> TransientError | PermanentError | None:
    if isinstance(exc, httpx.TimeoutException):
        return TransientError(_HTTP_TIMEOUT)
    if isinstance(exc, httpx.RequestError):
        return TransientError(_HTTP_REQUEST_ERROR)
    if isinstance(exc, httpx.HTTPStatusError):
        return _classify_http_status(exc)
    return None

def _classify_local_exception(exc: BaseException) -> TransientError | PermanentError | None:
    if isinstance(exc, OSError):
        return _classify_os_error(exc)
    if isinstance(exc, (ValueError, TypeError)):
        return PermanentError(str(exc) or exc.__class__.__name__)
    return None
