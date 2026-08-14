"""Covers user-facing error-code, action-hint, and escaped-note rendering contracts."""

from __future__ import annotations

from chronikwerk.adapters.zammad.errors import (
    AuthError,
    NotFoundError,
    RateLimitError,
    ServerError,
)
from chronikwerk.app.jobs.ticket_notes import (
    ErrorNotePayload,
    action_hint,
    error_code_and_hint,
    error_note_html,
)
from chronikwerk.domain.errors import PermanentError, TransientError

# ---------------------------------------------------------------------------
# error_code_and_hint: one test per branch
# ---------------------------------------------------------------------------


class TestErrorCodeAndHint:
    """Groups mappings from processing failures to stable error codes and hints."""

    def test_archive_path_missing(self) -> None:
        exc = ValueError("custom_fields.archive_path is missing")
        code, hint = error_code_and_hint(exc)
        assert code == "missing_archive_path"
        assert "archive_path" in hint.lower()

    def test_archive_path_empty(self) -> None:
        exc = ValueError("archive_path must not be empty")
        code, hint = error_code_and_hint(exc)
        assert code == "empty_archive_path"
        assert "segment" in hint.lower()

    def test_archive_path_empty_all_segments(self) -> None:
        exc = ValueError("all segments were empty after strip")
        code, _ = error_code_and_hint(exc)
        assert code == "empty_archive_path"

    def test_archive_path_must_be_string(self) -> None:
        exc = TypeError("archive_path must be a string or list")
        code, hint = error_code_and_hint(exc)
        assert code == "invalid_archive_path"
        assert "string" in hint.lower()

    def test_archive_path_indexed_type(self) -> None:
        exc = TypeError("archive_path[2] must be a string, got int")
        code, _ = error_code_and_hint(exc)
        assert code == "invalid_archive_path"

    def test_missing_owner_login(self) -> None:
        exc = ValueError("owner.login is required")
        code, _ = error_code_and_hint(exc)
        assert code == "missing_user_login"

    def test_missing_updated_by_login(self) -> None:
        exc = ValueError("updated_by.login is required")
        code, _ = error_code_and_hint(exc)
        assert code == "missing_user_login"

    def test_missing_archive_user(self) -> None:
        exc = ValueError("archive_user is required in fixed mode")
        code, _ = error_code_and_hint(exc)
        assert code == "missing_archive_user"

    def test_missing_archive_user_mode(self) -> None:
        exc = ValueError("archive_user_mode is invalid")
        code, _ = error_code_and_hint(exc)
        assert code == "missing_archive_user"

    def test_invalid_filename_pattern(self) -> None:
        exc = ValueError("filename pattern is not valid")
        code, hint = error_code_and_hint(exc)
        assert code == "invalid_filename"
        assert "filename" in hint.lower()

    def test_invalid_filename_segment(self) -> None:
        exc = ValueError("filename segment cannot be blank")
        code, _ = error_code_and_hint(exc)
        assert code == "invalid_filename"

    def test_invalid_filename_must_not(self) -> None:
        exc = ValueError("filename must not contain slashes")
        code, _ = error_code_and_hint(exc)
        assert code == "invalid_filename"

    def test_path_segment_validation(self) -> None:
        exc = ValueError("path segment must not be empty")
        code, _ = error_code_and_hint(exc)
        assert code == "path_validation"

    def test_path_separators_validation(self) -> None:
        exc = ValueError("path separators not allowed in segments")
        code, _ = error_code_and_hint(exc)
        assert code == "path_validation"

    def test_dot_segments_validation(self) -> None:
        exc = ValueError("dot segments are forbidden in path")
        code, _ = error_code_and_hint(exc)
        assert code == "path_validation"

    def test_generic_fallback(self) -> None:
        exc = RuntimeError("something completely unexpected")
        code, hint = error_code_and_hint(exc)
        assert code == "permanent_error"
        assert hint == ""


# ---------------------------------------------------------------------------
# action_hint: one test per branch
# ---------------------------------------------------------------------------


class TestActionHint:
    """Groups action-hint mappings for operators resolving archived-ticket failures."""

    def test_transient_error(self) -> None:
        exc = ConnectionError("connection refused")
        result = action_hint(exc, classified=TransientError("conn refused"))
        assert "Transient" in result
        assert "pdf:sign" in result

    def test_auth_error(self) -> None:
        exc = AuthError("HTTP 401 Unauthorized")
        result = action_hint(exc, classified=PermanentError("auth"))
        assert "token" in result.lower() or "401" in result

    def test_not_found_error(self) -> None:
        exc = NotFoundError("ticket 999 not found")
        result = action_hint(exc, classified=PermanentError("not found"))
        assert "not found" in result.lower()

    def test_server_error(self) -> None:
        exc = ServerError("HTTP 500")
        result = action_hint(exc, classified=PermanentError("server"))
        assert "Upstream" in result

    def test_rate_limit_error(self) -> None:
        exc = RateLimitError("HTTP 429")
        result = action_hint(exc, classified=PermanentError("rate"))
        assert "Upstream" in result

    def test_permission_error(self) -> None:
        exc = PermissionError("denied")
        result = action_hint(exc, classified=PermanentError("perm"))
        assert "permission" in result.lower()

    def test_value_error(self) -> None:
        exc = ValueError("bad field")
        result = action_hint(exc, classified=PermanentError("val"))
        assert "validation" in result.lower() or "fields" in result.lower()

    def test_type_error(self) -> None:
        exc = TypeError("wrong type")
        result = action_hint(exc, classified=PermanentError("type"))
        assert "validation" in result.lower() or "fields" in result.lower()

    def test_generic_permanent(self) -> None:
        exc = RuntimeError("unknown boom")
        result = action_hint(exc, classified=PermanentError("boom"))
        assert "Non-retryable" in result

    def test_classified_none_falls_through(self) -> None:
        """When classified is None the function uses isinstance checks on exc."""
        exc = AuthError("HTTP 403")
        result = action_hint(exc, classified=None)
        assert "token" in result.lower() or "401" in result


# ---------------------------------------------------------------------------
# error_note_html: code/hint presence
# ---------------------------------------------------------------------------


class TestErrorNoteHtml:
    """Groups HTML-note rendering and escaping assertions for error reporting."""

    def test_code_and_hint_included(self) -> None:
        html = error_note_html(
            ErrorNotePayload(
                classification="permanent",
                message="boom",
                action="retry",
                request_id="r1",
                delivery_id="d1",
                timestamp_utc="2025-01-01T00:00:00Z",
                code="missing_archive_path",
                hint="Set archive_path",
            )
        )
        assert "missing_archive_path" in html
        assert "Set archive_path" in html

    def test_no_code_no_hint(self) -> None:
        html = error_note_html(
            ErrorNotePayload(
                classification="permanent",
                message="boom",
                action="retry",
                request_id=None,
                delivery_id=None,
                timestamp_utc="2025-01-01T00:00:00Z",
            )
        )
        assert "unknown" in html  # request_id fallback
        assert "none" in html  # delivery_id fallback
