from __future__ import annotations

from pathlib import Path

import pytest

from zammad_pdf_archiver.adapters.storage.layout import (
    build_filename,
    build_filename_from_pattern,
    build_target_dir,
)


def test_build_target_dir_is_deterministic_and_safe() -> None:
    root = Path("/archive/root")
    out = build_target_dir(root, "user@example.com", ["My Team", "Tickets"])
    assert out == Path("/archive/root/user_example.com/My_Team/Tickets")


def test_build_target_dir_rejects_traversal_attempts() -> None:
    root = Path("/archive/root")
    with pytest.raises(ValueError):
        build_target_dir(root, "user", [".."])
    with pytest.raises(ValueError):
        build_target_dir(root, "user", ["a/b"])


def test_build_target_dir_sanitizes_unicode_only_segment() -> None:
    root = Path("/archive/root")
    out = build_target_dir(root, "user", ["你好"])
    assert out == Path("/archive/root/user/_")


def test_build_target_dir_enforces_allow_prefixes() -> None:
    root = Path("/archive/root")
    out = build_target_dir(
        root,
        "user@example.com",
        ["Customers", "ACME GmbH", "2026"],
        allow_prefixes=["Customers > ACME GmbH"],
    )
    assert out == Path("/archive/root/user_example.com/Customers/ACME_GmbH/2026")

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
    assert out == Path("/archive/root/user_example.com/Customers/ACME_GmbH/2026")


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
    assert out == Path("/archive/root/user/Muller/2026")


def test_build_filename_is_deterministic() -> None:
    assert build_filename(123, "2026-02-07", "Hello world") == "123-2026-02-07-Hello_world"


def test_build_filename_sanitizes_path_separators() -> None:
    assert build_filename("123", "2026-02-07", "hello/there") == "123-2026-02-07-hello_there"


# -- build_filename_from_pattern ---------------------------------------------------


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
    assert result == "12_34.pdf"


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
    assert result == Path("/archive/root/user/any/path")


def test_build_target_dir_allow_prefixes_whitespace_only_entry() -> None:
    root = Path("/archive/root")
    with pytest.raises(ValueError, match="non-empty strings"):
        build_target_dir(
            root,
            "user",
            ["A"],
            allow_prefixes=["   "],
        )
