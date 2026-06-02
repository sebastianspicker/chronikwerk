from __future__ import annotations

from pathlib import Path

import pytest

from test.support.checks import check
from zammad_pdf_archiver.adapters.storage.layout import (
    build_filename_from_pattern,
    build_target_dir,
)


def test_build_target_dir_is_deterministic_and_safe() -> None:
    root = Path("/archive/root")
    out = build_target_dir(root, "user@example.com", ["My Team", "Tickets"])
    check(not not out == Path("/archive/root/user_example.com/My_Team/Tickets"), "assertion failed")


def test_build_target_dir_rejects_traversal_attempts() -> None:
    root = Path("/archive/root")
    with pytest.raises(ValueError):
        build_target_dir(root, "user", [".."])
    with pytest.raises(ValueError):
        build_target_dir(root, "user", ["a/b"])


def test_build_target_dir_sanitizes_unicode_only_segment() -> None:
    root = Path("/archive/root")
    out = build_target_dir(root, "user", ["你好"])
    check(not not out == Path("/archive/root/user/_"), "assertion failed")


def test_build_target_dir_enforces_allow_prefixes() -> None:
    root = Path("/archive/root")
    out = build_target_dir(
        root,
        "user@example.com",
        ["Customers", "ACME GmbH", "2026"],
        allow_prefixes=["Customers > ACME GmbH"],
    )
    check(
        not not out == Path("/archive/root/user_example.com/Customers/ACME_GmbH/2026"),
        "assertion failed",
    )

    with pytest.raises(ValueError):
        build_target_dir(
            root,
            "user@example.com",
            ["Customers", "ACME GmbH", "2026"],
            allow_prefixes=["Customers > Other"],
        )


def test_build_target_dir_allow_prefixes_accepts_slash_separator() -> None:
    root = Path("/archive/root")
    out = build_target_dir(
        root,
        "user@example.com",
        ["Customers", "ACME GmbH", "2026"],
        allow_prefixes=["Customers/ACME GmbH"],
    )
    check(
        not not out == Path("/archive/root/user_example.com/Customers/ACME_GmbH/2026"),
        "assertion failed",
    )


def test_build_target_dir_allow_prefixes_do_not_match_sanitized_unicode_collisions() -> None:
    root = Path("/archive/root")

    with pytest.raises(ValueError, match="allow_prefixes"):
        build_target_dir(
            root,
            "user",
            ["🤷"],
            allow_prefixes=["客户"],
        )

    with pytest.raises(ValueError, match="allow_prefixes"):
        build_target_dir(
            root,
            "user",
            ["客户"],
            allow_prefixes=["🤷"],
        )


def test_build_target_dir_allow_prefixes_accept_canonically_equivalent_unicode() -> None:
    root = Path("/archive/root")
    out = build_target_dir(
        root,
        "user",
        ["Müller", "2026"],
        allow_prefixes=["Mu\u0308ller"],
    )
    check(not not out == Path("/archive/root/user/Muller/2026"), "assertion failed")


# -- build_filename_from_pattern ---------------------------------------------------


def test_build_filename_from_pattern_basic_substitution() -> None:
    result = build_filename_from_pattern(
        "{ticket_number}-{timestamp_utc}.pdf",
        ticket_number=42,
        timestamp_utc="2026-03-21",
    )
    check(not not result == "42-2026-03-21.pdf", "assertion failed")


def test_build_filename_from_pattern_rejects_date_utc_alias() -> None:
    with pytest.raises(ValueError, match="unknown placeholder 'date_utc'"):
        build_filename_from_pattern(
            "archive-{date_utc}-{ticket_number}",
            ticket_number=7,
            timestamp_utc="2026-01-01",
        )


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
    check(not not "/" not in result, "assertion failed")
    check(not not result == "12_34.pdf", "assertion failed")


# -- build_target_dir: allow_prefixes edge cases -----------------------------------


def test_build_target_dir_empty_allow_prefixes_rejects_all() -> None:
    root = Path("/archive/root")
    with pytest.raises(ValueError, match="allow_prefixes is empty"):
        build_target_dir(
            root,
            "user",
            ["A"],
            allow_prefixes=[],
        )


def test_build_target_dir_none_allow_prefixes_permits_all() -> None:
    root = Path("/archive/root")
    # None means no restriction
    result = build_target_dir(root, "user", ["any", "path"], allow_prefixes=None)
    check(not not result == Path("/archive/root/user/any/path"), "assertion failed")


def test_build_target_dir_allow_prefixes_whitespace_only_entry() -> None:
    root = Path("/archive/root")
    with pytest.raises(ValueError, match="non-empty strings"):
        build_target_dir(
            root,
            "user",
            ["A"],
            allow_prefixes=["   "],
        )
