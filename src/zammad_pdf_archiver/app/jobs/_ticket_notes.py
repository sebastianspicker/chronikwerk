from html import escape

import structlog

from zammad_pdf_archiver._version import VERSION
from zammad_pdf_archiver.adapters.zammad.errors import (
    AuthError,
    NotFoundError,
)
from zammad_pdf_archiver.domain.archive_errors import ArchiveUserInputError
from zammad_pdf_archiver.domain.errors import PermanentError, TransientError
from zammad_pdf_archiver.domain.exc_format import bounded_exc_message

log = structlog.get_logger(__name__)


def _html_field_list(heading: str, fields: list[tuple[str, str]]) -> str:
    """Build an HTML note with a heading and escaped key/value list."""
    items = "".join(
        f"<li>{label}: <code>{escape(str(value))}</code></li>" for label, value in fields
    )
    return f"<p><strong>{escape(heading)}</strong></p><ul>{items}</ul>"


def success_note_html(
    *,
    storage_dir: str,
    filename: str,
    sidecar_path: str,
    size_bytes: int,
    sha256_hex: str,
    request_id: str | None,
    delivery_id: str | None,
    timestamp_utc: str,
) -> str:
    """Return an HTML note body summarising a successful PDF archival operation."""
    return _html_field_list(
        f"PDF archived ({VERSION})",
        [
            ("path", storage_dir),
            ("filename", filename),
            ("audit_sidecar", sidecar_path),
            ("size_bytes", str(size_bytes)),
            ("sha256", sha256_hex),
            ("request_id", request_id or "unknown"),
            ("delivery_id", delivery_id or "none"),
            ("time_utc", timestamp_utc),
        ],
    )


def error_code_and_hint(exc: BaseException) -> tuple[str, str]:
    """Return (stable_code, short_hint) for permanent failures."""
    if isinstance(exc, ArchiveUserInputError):
        return (exc.code, exc.hint)
    return ("permanent_error", "")


def error_note_html(
    *,
    classification: str,
    message: str,
    action: str,
    request_id: str | None,
    delivery_id: str | None,
    timestamp_utc: str,
    code: str = "",
    hint: str = "",
) -> str:
    """Return an HTML note body describing an archival failure with classification and hints."""
    fields: list[tuple[str, str]] = [
        ("classification", classification),
        ("error", message),
        ("action", action),
    ]
    if code:
        fields.append(("code", code))
    if hint:
        fields.append(("hint", hint))
    fields.extend(
        [
            ("request_id", request_id or "unknown"),
            ("delivery_id", delivery_id or "none"),
            ("time_utc", timestamp_utc),
        ]
    )
    return _html_field_list(f"PDF archiver error ({VERSION})", fields)


def concise_exc_message(exc: BaseException) -> str:
    return bounded_exc_message(exc)


def action_hint(exc: BaseException, *, classified: TransientError | PermanentError) -> str:
    """Return a human-readable operator action hint for the given exception and classification."""
    if isinstance(classified, TransientError):
        return (
            "Transient failure. Verify Zammad/TSA reachability and storage availability; "
            "the ticket keeps pdf:sign so a retry can be triggered by saving the ticket "
            "or reapplying the macro."
        )

    # PermanentError: aim for a concrete operator action.
    if isinstance(exc, AuthError):
        return "Fix Zammad API token/permissions (HTTP 401/403), then reapply the pdf:sign macro."
    if isinstance(exc, NotFoundError):
        return (
            "Ticket/resource not found in Zammad. Verify the ticket still exists, then reapply "
            "pdf:sign."
        )
    if isinstance(exc, PermissionError):
        return (
            "Storage permission denied. Check network share mount options, ownership, and ACLs, "
            "then reapply the pdf:sign macro."
        )
    if isinstance(exc, (ValueError, TypeError)):
        return (
            "Fix ticket fields / path policy validation, then reapply the pdf:sign macro "
            "(and optionally remove pdf:error for clarity)."
        )
    return (
        "Non-retryable failure by policy. Fix the underlying issue and reapply the pdf:sign macro "
        "(and optionally remove pdf:error)."
    )
