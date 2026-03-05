from html import escape

import structlog

from zammad_pdf_archiver._version import VERSION
from zammad_pdf_archiver.adapters.zammad.errors import (
    AuthError,
    NotFoundError,
    RateLimitError,
    ServerError,
)
from zammad_pdf_archiver.config.redact import scrub_secrets_in_text
from zammad_pdf_archiver.domain.errors import PermanentError, TransientError

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
    """Return (stable_code, short_hint) for permanent failures (Bug #7)."""
    msg = str(exc).strip().lower()
    if "archive_path is missing" in msg or "archive_path" in msg and "missing" in msg:
        return ("missing_archive_path", "Set custom_fields.archive_path on the ticket.")
    if "archive_path must not be empty" in msg or "all segments were empty" in msg:
        return ("empty_archive_path", "Set archive_path to at least one non-empty segment.")
    if "archive_path must be a string" in msg or "archive_path[" in msg:
        return ("invalid_archive_path", "Use a string or list of strings for archive_path.")
    if "allow_prefixes" in msg and "not allowed" in msg:
        return ("path_not_allowed", "Check allow_prefixes; archive_path must match a prefix.")
    if "allow_prefixes is empty" in msg:
        return (
            "allow_prefixes_empty",
            "Configure at least one allow_prefixes entry or leave unset.",
        )
    if "owner.login" in msg or "updated_by.login" in msg:
        return ("missing_user_login", "Ensure ticket has owner/updated_by with login.")
    if "archive_user" in msg or "archive_user_mode" in msg:
        return ("missing_archive_user", "Set custom_fields.archive_user for fixed mode.")
    if "filename" in msg and ("pattern" in msg or "segment" in msg or "must not" in msg):
        return (
            "invalid_filename",
            "Check filename_pattern and path policy (no ., .., separators).",
        )
    if "path segment" in msg or "path separators" in msg or "dot segments" in msg:
        return ("path_validation", "Check archive_path segments (no ., .., empty, or separators).")
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
    text = f"{exc.__class__.__name__}: {exc}"
    text = text.strip()
    text = scrub_secrets_in_text(text)
    return text[:500] if len(text) > 500 else text


def action_hint(exc: BaseException, *, classified: TransientError | PermanentError | None) -> str:
    if classified is not None and isinstance(classified, TransientError):
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
    if isinstance(exc, (ServerError, RateLimitError)):
        return (
            "Upstream Zammad error was treated as permanent by policy. "
            "If the issue is resolved, reapply the pdf:sign macro to reprocess."
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
