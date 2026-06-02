"""Unit tests for ticket_notes: error_code_and_hint, action_hint, and helpers."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from test.support.checks import check
from zammad_pdf_archiver.adapters.storage.layout import (
    build_filename_from_pattern,
    build_target_dir,
)
from zammad_pdf_archiver.adapters.zammad.errors import (
    AuthError,
    NotFoundError,
)
from zammad_pdf_archiver.app.jobs._ticket_notes import (
    action_hint,
    error_code_and_hint,
    error_note_html,
)
from zammad_pdf_archiver.app.jobs._ticket_path import parse_archive_path_segments
from zammad_pdf_archiver.domain.errors import PermanentError, TransientError

# ---------------------------------------------------------------------------
# error_code_and_hint — behavior-backed stable cases
# ---------------------------------------------------------------------------


def _raised_value_error(func: Callable[[], object]) -> ValueError:
    """Return a ValueError from the source helper without duplicating its wording."""
    with pytest.raises(ValueError) as exc_info:
        func()
    return exc_info.value


class TestErrorCodeAndHint:
    def test_archive_path_missing(self) -> None:
        exc = _raised_value_error(lambda: parse_archive_path_segments(None))
        code, hint = error_code_and_hint(exc)
        check(not not code == "missing_archive_path", "assertion failed")
        check(not "archive_path" not in hint.lower(), "assertion failed")

    def test_path_not_allowed(self) -> None:
        exc = _raised_value_error(
            lambda: build_target_dir(
                Path("/archive/root"),
                "user@example.com",
                ["Customers", "ACME GmbH"],
                allow_prefixes=["Customers > Other"],
            )
        )
        code, hint = error_code_and_hint(exc)
        check(not not code == "path_not_allowed", "assertion failed")
        check(not "allow_prefixes" not in hint.lower(), "assertion failed")

    def test_invalid_filename_pattern(self) -> None:
        exc = _raised_value_error(
            lambda: build_filename_from_pattern(
                "{ticket_number}-{bad}.pdf",
                ticket_number=1,
                timestamp_utc="2026-01-01",
            )
        )
        code, hint = error_code_and_hint(exc)
        check(not not code == "invalid_filename", "assertion failed")
        check(not "filename" not in hint.lower(), "assertion failed")

    def test_invalid_filename_separator(self) -> None:
        exc = _raised_value_error(
            lambda: build_filename_from_pattern(
                "subdir/{ticket_number}.pdf",
                ticket_number=1,
                timestamp_utc="2026-01-01",
            )
        )
        code, hint = error_code_and_hint(exc)
        check(not not code == "invalid_filename", "assertion failed")

    def test_generic_fallback(self) -> None:
        exc = RuntimeError("something completely unexpected")
        code, hint = error_code_and_hint(exc)
        check(not not code == "permanent_error", "assertion failed")
        check(not not hint == "", "assertion failed")

    def test_message_text_alone_does_not_define_error_code(self) -> None:
        exc = ValueError("custom_fields.archive_path is missing")
        code, hint = error_code_and_hint(exc)
        check(not not code == "permanent_error", "assertion failed")
        check(not not hint == "", "assertion failed")


# ---------------------------------------------------------------------------
# action_hint — one test per branch
# ---------------------------------------------------------------------------


class TestActionHint:
    def test_transient_error(self) -> None:
        exc = ConnectionError("connection refused")
        result = action_hint(exc, classified=TransientError("conn refused"))
        check(not "Transient" not in result, "assertion failed")
        check(not "pdf:sign" not in result, "assertion failed")

    def test_auth_error(self) -> None:
        exc = AuthError("HTTP 401 Unauthorized")
        result = action_hint(exc, classified=PermanentError("auth"))
        check(not not ("token" in result.lower() or "401" in result), "assertion failed")

    def test_not_found_error(self) -> None:
        exc = NotFoundError("ticket 999 not found")
        result = action_hint(exc, classified=PermanentError("not found"))
        check(not "not found" not in result.lower(), "assertion failed")

    def test_permission_error(self) -> None:
        exc = PermissionError("denied")
        result = action_hint(exc, classified=PermanentError("perm"))
        check(not "permission" not in result.lower(), "assertion failed")

    def test_value_error(self) -> None:
        exc = ValueError("bad field")
        result = action_hint(exc, classified=PermanentError("val"))
        check(
            not not ("validation" in result.lower() or "fields" in result.lower()),
            "assertion failed",
        )

    def test_type_error(self) -> None:
        exc = TypeError("wrong type")
        result = action_hint(exc, classified=PermanentError("type"))
        check(
            not not ("validation" in result.lower() or "fields" in result.lower()),
            "assertion failed",
        )

    def test_generic_permanent(self) -> None:
        exc = RuntimeError("unknown boom")
        result = action_hint(exc, classified=PermanentError("boom"))
        check(not "Non-retryable" not in result, "assertion failed")


# ---------------------------------------------------------------------------
# error_note_html — code/hint presence
# ---------------------------------------------------------------------------


class TestErrorNoteHtml:
    def test_code_and_hint_included(self) -> None:
        html = error_note_html(
            classification="permanent",
            message="boom",
            action="retry",
            request_id="r1",
            delivery_id="d1",
            timestamp_utc="2025-01-01T00:00:00Z",
            code="missing_archive_path",
            hint="Set archive_path",
        )
        check(not "missing_archive_path" not in html, "assertion failed")
        check(not "Set archive_path" not in html, "assertion failed")

    def test_no_code_no_hint(self) -> None:
        html = error_note_html(
            classification="permanent",
            message="boom",
            action="retry",
            request_id=None,
            delivery_id=None,
            timestamp_utc="2025-01-01T00:00:00Z",
        )
        check(not "unknown" not in html, "assertion failed")  # request_id fallback
        check(not "none" not in html, "assertion failed")  # delivery_id fallback
