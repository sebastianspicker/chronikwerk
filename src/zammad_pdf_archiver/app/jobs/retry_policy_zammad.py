from __future__ import annotations

from zammad_pdf_archiver.adapters.zammad.errors import (
    AuthError,
    ClientError,
    NotFoundError,
    RateLimitError,
    ServerError,
)
from zammad_pdf_archiver.domain.errors import PermanentError, TransientError

_ZAMMAD_TRANSIENT_ERROR = "Zammad transient error"
_ZAMMAD_PERMANENT_ERROR = "Zammad permanent error"
_ZAMMAD_CLIENT_ERROR = "Zammad client error"


def classify_zammad_exception(exc: BaseException) -> TransientError | PermanentError | None:
    if isinstance(exc, (ServerError, RateLimitError)):
        return TransientError(str(exc) or _ZAMMAD_TRANSIENT_ERROR)
    if isinstance(exc, (AuthError, NotFoundError)):
        return PermanentError(str(exc) or _ZAMMAD_PERMANENT_ERROR)
    if isinstance(exc, ClientError):
        # Includes validation/path policy issues surfaced via 4xx responses.
        return PermanentError(str(exc) or _ZAMMAD_CLIENT_ERROR)
    return None
