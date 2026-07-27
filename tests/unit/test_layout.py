"""Verifies archive paths and filenames are deterministic, safe, and collision-aware."""

from __future__ import annotations

from pathlib import Path

import pytest

from chronikwerk.adapters.storage.layout import (
    build_filename_from_pattern,
    build_target_dir,
)


def test_build_target_dir_is_deterministic_and_safe() -> None:
    root = Path("/archive/root")
    out = build_target_dir(root, "user@example.com", ["My Team", "Tickets"])
    assert out.parent.parent.parent == root
    assert out.parts[-3].startswith("user_example.com-")
    assert out.parts[-2].startswith("My_Team-")
    assert out.name == "Tickets"


def test_build_target_dir_rejects_traversal_attempts() -> None:
    root = Path("/archive/root")
    with pytest.raises(ValueError):
        build_target_dir(root, "user", [".."])
    with pytest.raises(ValueError):
        build_target_dir(root, "user", ["a/b"])


def test_build_target_dir_sanitizes_unicode_only_segment() -> None:
    root = Path("/archive/root")
    out = build_target_dir(root, "user", ["你好"])
    assert out.parent == root / "user"
    assert out.name.startswith("_-")


def test_build_target_dir_disambiguates_lossy_sanitization_collisions() -> None:
    root = Path("/archive/root")

    first = build_target_dir(root, "alice+hr", ["A+B"])
    second = build_target_dir(root, "alice?hr", ["A?B"])

    assert first != second
    assert first.parent.name != second.parent.name
    assert first.name != second.name


def test_build_filename_from_pattern_basic_substitution() -> None:
    result = build_filename_from_pattern(
        "{ticket_number}-{timestamp_utc}.pdf",
        ticket_number=42,
        timestamp_utc="2026-03-21",
    )
    assert result == "42-2026-03-21.pdf"


def test_build_filename_from_pattern_date_utc_alias() -> None:
    result = build_filename_from_pattern(
        "archive-{date_utc}-{ticket_number}",
        ticket_number=7,
        timestamp_utc="2026-01-01",
    )
    assert result == "archive-2026-01-01-7"


def test_build_filename_from_pattern_unknown_placeholder() -> None:
    with pytest.raises(ValueError, match="unknown placeholder"):
        build_filename_from_pattern(
            "{ticket_number}-{bogus}",
            ticket_number=1,
            timestamp_utc="2026-01-01",
        )


def test_build_filename_from_pattern_empty_pattern() -> None:
    with pytest.raises(ValueError, match="non-empty string"):
        build_filename_from_pattern("", ticket_number=1, timestamp_utc="2026-01-01")

    with pytest.raises(ValueError, match="non-empty string"):
        build_filename_from_pattern("   ", ticket_number=1, timestamp_utc="2026-01-01")


def test_build_filename_from_pattern_rejects_separators() -> None:
    with pytest.raises(ValueError, match="path separators"):
        build_filename_from_pattern(
            "sub/{ticket_number}.pdf",
            ticket_number=1,
            timestamp_utc="2026-01-01",
        )


def test_build_filename_from_pattern_rejects_dot_segments() -> None:
    with pytest.raises(ValueError, match="must not be"):
        build_filename_from_pattern(
            "..",
            ticket_number=1,
            timestamp_utc="2026-01-01",
        )


def test_build_filename_from_pattern_sanitizes_special_chars() -> None:
    result = build_filename_from_pattern(
        "{ticket_number}.pdf",
        ticket_number="12/34",
        timestamp_utc="2026-01-01",
    )
    # sanitize_segment replaces "/" with "_"
    assert "/" not in result
    assert result.startswith("12_34-")
    assert result.endswith(".pdf")


def test_build_filename_disambiguates_lossy_ticket_number_collisions() -> None:
    first = build_filename_from_pattern(
        "{ticket_number}-{timestamp_utc}.pdf",
        ticket_number="A+B",
        timestamp_utc="2026-03-21",
    )
    second = build_filename_from_pattern(
        "{ticket_number}-{timestamp_utc}.pdf",
        ticket_number="A?B",
        timestamp_utc="2026-03-21",
    )

    assert first != second
