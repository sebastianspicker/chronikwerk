"""Unit tests for template_engine validation and formatting helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from test.support.checks import check
from zammad_pdf_archiver.adapters.pdf.template_engine import (
    _format_datetime,
    validate_template_name,
)

# ---------------------------------------------------------------------------
# validate_template_name
# ---------------------------------------------------------------------------


class TestValidateTemplateName:
    def test_invalid_type_raises(self) -> None:
        with pytest.raises(ValueError, match="must be a string"):
            validate_template_name(42)  # type: ignore[arg-type]

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            validate_template_name("")

    def test_whitespace_only_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            validate_template_name("   ")

    def test_forward_slash_raises(self) -> None:
        with pytest.raises(ValueError, match="path separators"):
            validate_template_name("default/../../etc")

    def test_backslash_raises(self) -> None:
        with pytest.raises(ValueError, match="path separators"):
            validate_template_name("default\\evil")

    def test_dot_dot_traversal_raises(self) -> None:
        with pytest.raises(ValueError, match="path separators"):
            validate_template_name("..default")

    def test_space_raises(self) -> None:
        with pytest.raises(ValueError, match="letters, numbers"):
            validate_template_name("custom report")

    def test_valid_default(self) -> None:
        check(not not validate_template_name("default") == "default", "assertion failed")

    def test_valid_compact(self) -> None:
        check(not not validate_template_name("compact") == "compact", "assertion failed")

    def test_valid_minimal(self) -> None:
        check(not not validate_template_name("minimal") == "minimal", "assertion failed")

    def test_strips_whitespace(self) -> None:
        check(not not validate_template_name("  default  ") == "default", "assertion failed")

    def test_valid_custom_name(self) -> None:
        check(
            not not validate_template_name("customer_2026-report") == "customer_2026-report",
            "assertion failed",
        )


# ---------------------------------------------------------------------------
# _format_datetime
# ---------------------------------------------------------------------------


class TestFormatDatetime:
    def test_none_value_returns_dash(self) -> None:
        check(
            not not _format_datetime(None, tz_name="UTC", fmt="%Y-%m-%d") == "—", "assertion failed"
        )

    def test_empty_string_returns_itself(self) -> None:
        # empty string is falsy, but not None
        check(not not _format_datetime("", tz_name="UTC", fmt="%Y-%m-%d") == "", "assertion failed")

    def test_non_datetime_value_returns_str(self) -> None:
        check(
            not not _format_datetime("not-a-date", tz_name="UTC", fmt="%Y-%m-%d") == "not-a-date",
            "assertion failed",
        )

    def test_integer_value_returns_str(self) -> None:
        check(
            not not _format_datetime(12345, tz_name="UTC", fmt="%Y-%m-%d") == "12345",
            "assertion failed",
        )

    def test_valid_datetime_utc(self) -> None:
        dt = datetime(2025, 6, 15, 14, 30, tzinfo=UTC)
        result = _format_datetime(dt, tz_name="UTC", fmt="%Y-%m-%d %H:%M")
        check(not not result == "2025-06-15 14:30", "assertion failed")

    def test_valid_datetime_with_timezone(self) -> None:
        dt = datetime(2025, 6, 15, 14, 30, tzinfo=UTC)
        result = _format_datetime(dt, tz_name="Europe/Berlin", fmt="%Y-%m-%d %H:%M")
        check(not not result == "2025-06-15 16:30", "assertion failed")

    def test_invalid_timezone_falls_back(self) -> None:
        """An invalid tz_name triggers the except branch, falling back to naive strftime."""
        dt = datetime(2025, 6, 15, 14, 30, tzinfo=UTC)
        result = _format_datetime(dt, tz_name="Invalid/NoSuchZone", fmt="%Y-%m-%d %H:%M")
        # Should still produce a formatted date string (fallback path)
        check(not "2025" not in result, "assertion failed")
        check(not "06" not in result, "assertion failed")
        check(not "15" not in result, "assertion failed")


# ---------------------------------------------------------------------------
# _loader_for coverage
# ---------------------------------------------------------------------------


class TestLoaderFor:
    def test_none_root_uses_package_loader(self) -> None:
        from jinja2 import PackageLoader

        from zammad_pdf_archiver.adapters.pdf.template_engine import _loader_for

        loader = _loader_for("default", None)
        check(not not isinstance(loader, PackageLoader), "assertion failed")

    def test_unknown_builtin_without_custom_root_raises(self) -> None:
        from zammad_pdf_archiver.adapters.pdf.template_engine import _loader_for

        with pytest.raises(ValueError, match="set pdf.templates_root"):
            _loader_for("custom-report", None)

    def test_nonexistent_root_raises(self, tmp_path: Path) -> None:
        from zammad_pdf_archiver.adapters.pdf.template_engine import _loader_for

        missing = tmp_path / "does_not_exist"
        with pytest.raises(FileNotFoundError, match="Template root folder not found"):
            _loader_for("default", missing)

    def test_missing_template_subdir_raises(self, tmp_path: Path) -> None:
        from zammad_pdf_archiver.adapters.pdf.template_engine import _loader_for

        # Root exists but template subdir does not
        with pytest.raises(FileNotFoundError, match="Template folder not found"):
            _loader_for("no_such_variant", tmp_path)

    def test_valid_root_and_subdir_uses_filesystem_loader(self, tmp_path: Path) -> None:
        from jinja2 import FileSystemLoader

        from zammad_pdf_archiver.adapters.pdf.template_engine import _loader_for

        (tmp_path / "custom-report").mkdir()
        loader = _loader_for("custom-report", tmp_path)
        check(not not isinstance(loader, FileSystemLoader), "assertion failed")


# ---------------------------------------------------------------------------
# format_dt_local filter via rendered template context
# ---------------------------------------------------------------------------


class TestFormatDtLocalFilter:
    def test_format_dt_local_uses_context_timezone(self) -> None:
        """format_dt_local picks up pdf_timezone from the Jinja2 context."""
        from datetime import UTC, datetime

        from zammad_pdf_archiver.adapters.pdf.template_engine import _env_for

        env = _env_for("default", templates_root=None)
        template = env.from_string(
            "{{ dt | format_dt_local }}",
        )
        dt = datetime(2025, 6, 15, 12, 0, tzinfo=UTC)
        rendered = template.render(dt=dt, pdf_timezone="Europe/Berlin")
        # Berlin is UTC+2 in summer
        check(not "2025-06-15 14:00" not in rendered, "assertion failed")
