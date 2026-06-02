from __future__ import annotations

from zammad_pdf_archiver.config.redact import scrub_secrets_in_text


def bounded_exc_message(exc: BaseException | str, max_len: int = 500) -> str:
    """Return a scrubbed, bounded error message for notes, history, and queue fields."""
    if isinstance(exc, BaseException):
        text = f"{exc.__class__.__name__}: {exc}"
    else:
        text = exc
    cleaned = scrub_secrets_in_text(str(text).strip())
    return cleaned[:max_len]
