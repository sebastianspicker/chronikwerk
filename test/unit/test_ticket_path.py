from __future__ import annotations

import pytest

from zammad_pdf_archiver.adapters.zammad.models import Ticket, UserRef
from zammad_pdf_archiver.app.jobs.ticket_path import (
    determine_username,
    parse_archive_path_segments,
    require_nonempty,
)

# ---------------------------------------------------------------------------
# require_nonempty
# ---------------------------------------------------------------------------


def test_require_nonempty_valid() -> None:
    assert require_nonempty("hello", field="x") == "hello"


def test_require_nonempty_empty() -> None:
    with pytest.raises(ValueError, match="must be non-empty"):
        require_nonempty("", field="x")


@pytest.mark.parametrize("value", [42, None, 3.14])
def test_require_nonempty_non_string(value: object) -> None:
    with pytest.raises(ValueError, match="must be a string"):
        require_nonempty(value, field="x")


# ---------------------------------------------------------------------------
# determine_username — helpers
# ---------------------------------------------------------------------------


def _ticket(
    *,
    owner_login: str | None = None,
    has_owner: bool = True,
    updated_by_login: str | None = None,
    has_updated_by: bool = True,
) -> Ticket:
    owner = UserRef(login=owner_login) if has_owner else None
    updated_by = UserRef(login=updated_by_login) if has_updated_by else None
    return Ticket(id=1, number="10001", owner=owner, updated_by=updated_by)


# ---------------------------------------------------------------------------
# determine_username — owner mode
# ---------------------------------------------------------------------------


def test_determine_username_owner_mode() -> None:
    ticket = _ticket(owner_login="agent-jane")
    result = determine_username(
        ticket=ticket,
        payload={},
        custom_fields={},
        mode_field_name="archive_user_mode",
    )
    assert result == "agent-jane"


def test_determine_username_owner_mode_no_owner() -> None:
    ticket = _ticket(has_owner=False)
    with pytest.raises(ValueError, match="owner"):
        determine_username(
            ticket=ticket,
            payload={},
            custom_fields={},
            mode_field_name="archive_user_mode",
        )


# ---------------------------------------------------------------------------
# determine_username — current_agent mode
# ---------------------------------------------------------------------------


def test_determine_username_current_agent_mode() -> None:
    ticket = _ticket(updated_by_login="agent-bob")
    result = determine_username(
        ticket=ticket,
        payload={"user": {"login": "payload-user"}},
        custom_fields={"archive_user_mode": "current_agent"},
        mode_field_name="archive_user_mode",
    )
    assert result == "payload-user"


def test_determine_username_current_agent_no_user() -> None:
    ticket = _ticket(has_updated_by=False)
    with pytest.raises(ValueError, match="updated_by"):
        determine_username(
            ticket=ticket,
            payload={},
            custom_fields={"archive_user_mode": "current_agent"},
            mode_field_name="archive_user_mode",
        )


# ---------------------------------------------------------------------------
# determine_username — fixed mode
# ---------------------------------------------------------------------------


def test_determine_username_fixed_mode() -> None:
    result = determine_username(
        ticket=_ticket(),
        payload={},
        custom_fields={
            "archive_user_mode": "fixed",
            "archive_user": "static-agent",
        },
        mode_field_name="archive_user_mode",
    )
    assert result == "static-agent"


def test_determine_username_fixed_missing_field() -> None:
    with pytest.raises(ValueError, match="archive_user"):
        determine_username(
            ticket=_ticket(),
            payload={},
            custom_fields={"archive_user_mode": "fixed"},
            mode_field_name="archive_user_mode",
        )


# ---------------------------------------------------------------------------
# determine_username — unsupported mode
# ---------------------------------------------------------------------------


def test_determine_username_unsupported_mode() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        determine_username(
            ticket=_ticket(),
            payload={},
            custom_fields={"archive_user_mode": "bogus"},
            mode_field_name="archive_user_mode",
        )


# ---------------------------------------------------------------------------
# parse_archive_path_segments
# ---------------------------------------------------------------------------


def test_parse_string_with_separator() -> None:
    assert parse_archive_path_segments("a>b>c") == ["a", "b", "c"]


def test_parse_valid_list() -> None:
    assert parse_archive_path_segments(["a", "b"]) == ["a", "b"]


def test_parse_none() -> None:
    with pytest.raises(ValueError, match="missing"):
        parse_archive_path_segments(None)


def test_parse_list_with_non_string() -> None:
    with pytest.raises(ValueError, match="must be a string"):
        parse_archive_path_segments([1, "a"])


def test_parse_empty_result() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        parse_archive_path_segments("")
