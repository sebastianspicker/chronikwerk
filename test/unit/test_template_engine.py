"""Unit tests for template_engine validation and formatting helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

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

    def test_unknown_name_raises(self) -> None:
        with pytest.raises(ValueError, match="must be one of"):
            validate_template_name("nonexistent")

    def test_valid_default(self) -> None:
        assert validate_template_name("default") == "default"

    def test_valid_compact(self) -> None:
        assert validate_template_name("compact") == "compact"

    def test_valid_minimal(self) -> None:
        assert validate_template_name("minimal") == "minimal"

    def test_strips_whitespace(self) -> None:
        assert validate_template_name("  default  ") == "default"


# ---------------------------------------------------------------------------
# _format_datetime
# ---------------------------------------------------------------------------


class TestFormatDatetime:
    def test_none_value_returns_dash(self) -> None:
        assert _format_datetime(None, tz_name="UTC", fmt="%Y-%m-%d") == "\u2014"

    def test_empty_string_returns_itself(self) -> None:
        # empty string is falsy, but not None
        assert _format_datetime("", tz_name="UTC", fmt="%Y-%m-%d") == ""

    def test_non_datetime_value_returns_str(self) -> None:
        assert _format_datetime("not-a-date", tz_name="UTC", fmt="%Y-%m-%d") == "not-a-date"

    def test_integer_value_returns_str(self) -> None:
        assert _format_datetime(12345, tz_name="UTC", fmt="%Y-%m-%d") == "12345"

    def test_valid_datetime_utc(self) -> None:
        dt = datetime(2025, 6, 15, 14, 30, tzinfo=UTC)
        result = _format_datetime(dt, tz_name="UTC", fmt="%Y-%m-%d %H:%M")
        assert result == "2025-06-15 14:30"

    def test_valid_datetime_with_timezone(self) -> None:
        dt = datetime(2025, 6, 15, 14, 30, tzinfo=UTC)
        result = _format_datetime(dt, tz_name="Europe/Berlin", fmt="%Y-%m-%d %H:%M")
        assert result == "2025-06-15 16:30"

    def test_invalid_timezone_falls_back(self) -> None:
        """An invalid tz_name triggers the except branch, falling back to naive strftime."""
        dt = datetime(2025, 6, 15, 14, 30, tzinfo=UTC)
        result = _format_datetime(dt, tz_name="Invalid/NoSuchZone", fmt="%Y-%m-%d %H:%M")
        # Should still produce a formatted date string (fallback path)
        assert "2025" in result
        assert "06" in result
        assert "15" in result


# ---------------------------------------------------------------------------
# _loader_for coverage
# ---------------------------------------------------------------------------


class TestLoaderFor:
    def test_none_root_uses_package_loader(self) -> None:
        from jinja2 import PackageLoader

        from zammad_pdf_archiver.adapters.pdf.template_engine import _loader_for

        loader = _loader_for("default", None)
        assert isinstance(loader, PackageLoader)

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

        (tmp_path / "default").mkdir()
        loader = _loader_for("default", tmp_path)
        assert isinstance(loader, FileSystemLoader)


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
        assert "2025-06-15 14:00" in rendered
