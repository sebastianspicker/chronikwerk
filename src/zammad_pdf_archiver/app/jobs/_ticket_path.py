from typing import Any

from zammad_pdf_archiver.adapters.zammad.models import Ticket
from zammad_pdf_archiver.domain.archive_errors import missing_archive_path_error


def require_nonempty(value: Any, *, field: str) -> str:
    """Return the stripped string value or raise ValueError if it is empty or not a string."""
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    out = value.strip()
    if not out:
        raise ValueError(f"{field} must be non-empty")
    return out


def determine_username(
    *,
    ticket: Ticket,
    payload: dict[str, Any],
    custom_fields: dict[str, Any],
    mode_field_name: str,
    archive_user_field_name: str = "archive_user",
) -> str:
    """Resolve the archive username from ticket data based on the configured mode field."""
    raw_mode = custom_fields.get(mode_field_name)
    mode = str(raw_mode).strip() if raw_mode is not None else "owner"

    if mode == "owner":
        return _owner_username(ticket)

    if mode == "current_agent":
        return _current_agent_username(ticket=ticket, payload=payload)

    if mode == "fixed":
        return _fixed_username(custom_fields, archive_user_field_name=archive_user_field_name)

    raise ValueError(f"unsupported archive_user_mode: {mode!r}")


def _owner_username(ticket: Ticket) -> str:
    login = ticket.owner.login if ticket.owner is not None else None
    return require_nonempty(login, field="ticket.owner.login")


def _current_agent_username(*, ticket: Ticket, payload: dict[str, Any]) -> str:
    login = _payload_user_login(payload)
    if login is not None:
        return login

    fallback = ticket.updated_by.login if ticket.updated_by is not None else None
    return require_nonempty(fallback, field="ticket.updated_by.login")


def _payload_user_login(payload: dict[str, Any]) -> str | None:
    user = payload.get("user")
    if not isinstance(user, dict):
        return None
    login = user.get("login")
    if isinstance(login, str) and login.strip():
        return login.strip()
    return None


def _fixed_username(
    custom_fields: dict[str, Any], *, archive_user_field_name: str
) -> str:
    return require_nonempty(
        custom_fields.get(archive_user_field_name),
        field=f"custom_fields.{archive_user_field_name}",
    )


def parse_archive_path_segments(value: Any) -> list[str]:
    """Parse and validate archive_path into a non-empty list of non-empty path segments."""
    if value is None:
        raise missing_archive_path_error()

    if isinstance(value, str):
        parts = _parse_archive_path_text(value)
    elif isinstance(value, list):
        parts = _parse_archive_path_list(value)
    else:
        raise ValueError("custom_fields.archive_path must be a string or list of strings")

    if not parts:
        raise ValueError(
            "custom_fields.archive_path must not be empty after sanitization "
            "(all segments were empty or whitespace-only)"
        )

    return parts


def _parse_archive_path_text(value: str) -> list[str]:
    return [part for part in (raw_part.strip() for raw_part in value.split(">")) if part]


def _parse_archive_path_list(value: list[Any]) -> list[str]:
    parts: list[str] = []
    for idx, item in enumerate(value):
        item = _archive_path_list_item(item, idx=idx)
        if item:
            parts.append(item)
    return parts


def _archive_path_list_item(item: Any, *, idx: int) -> str:
    if not isinstance(item, str):
        raise ValueError(f"custom_fields.archive_path[{idx}] must be a string")
    return item.strip()
