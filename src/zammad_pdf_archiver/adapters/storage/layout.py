from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import structlog

from zammad_pdf_archiver.domain.archive_errors import (
    invalid_filename_error,
    path_not_allowed_error,
)
from zammad_pdf_archiver.domain.path_policy import (
    ensure_within_root,
    sanitize_segment,
    validate_segments,
)

_PREFIX_SPLIT_RE = re.compile(r"[>/]")
log = structlog.get_logger(__name__)


def _parse_prefix_segments(prefix: str) -> list[str]:
    if not isinstance(prefix, str) or not prefix.strip():
        raise ValueError("allow_prefixes entries must be non-empty strings")

    raw_parts = [p.strip() for p in _PREFIX_SPLIT_RE.split(prefix)]
    parts = [p for p in raw_parts if p]
    if not parts:
        raise ValueError("allow_prefixes entry produced no segments")
    return parts


def _normalize_prefix_segment(segment: str) -> str:
    return unicodedata.normalize("NFC", segment)


def build_target_dir(
    root: Path,
    username: str,
    segments: list[str] | tuple[str, ...],
    *,
    allow_prefixes: list[str] | None = None,
) -> Path:
    """
    Build a deterministic directory path:
      ROOT / <sanitized-user> / <sanitized-segments...>

    This performs validation on raw inputs (rejects separators, dot segments, null bytes),
    then sanitizes segments for filesystem safety, then validates the sanitized output and
    ensures the final target is within ROOT.
    """
    if not isinstance(root, Path):
        root = Path(root)

    raw_segments = list(segments)
    validate_segments([username], max_depth=1)
    validate_segments(raw_segments)

    user_safe = sanitize_segment(username)
    segs_safe = [sanitize_segment(s) for s in raw_segments]

    validate_segments([user_safe], max_depth=1)
    validate_segments(segs_safe)

    _validate_allow_prefixes(raw_segments, allow_prefixes)

    target = root / user_safe
    for seg in segs_safe:
        target = target / seg

    ensure_within_root(root, target)
    return target


def build_filename_from_pattern(
    pattern: str,
    *,
    ticket_number: int | str,
    timestamp_utc: str,
) -> str:
    """
    Render a deterministic, filesystem-safe filename from a format string.

    Supported placeholders:
      - {ticket_number}
      - {timestamp_utc} (kept date-only for stability: YYYY-MM-DD)

    The rendered filename is validated to be a single safe path segment.
    """
    if not isinstance(pattern, str) or not pattern.strip():
        raise ValueError("pattern must be a non-empty string")

    ticket_safe = sanitize_segment(str(ticket_number))
    ts_safe = sanitize_segment(timestamp_utc)

    rendered = _render_filename_pattern(pattern, ticket_number=ticket_safe, timestamp_utc=ts_safe)

    rendered = rendered.strip()
    if not rendered:
        raise invalid_filename_error("filename_pattern produced an empty filename")

    # The filename pattern must create a filename only, never a directory or dot segment.
    if rendered in (".", ".."):
        raise invalid_filename_error("filename must not be '.' or '..'")

    # Disallow separators explicitly; patterns should not create directories.
    if "/" in rendered or "\\" in rendered or "\x00" in rendered:
        raise invalid_filename_error(
            "filename_pattern must not include path separators or null bytes"
        )

    validate_segments([rendered], max_depth=1, max_length=255)
    return rendered


def _validate_allow_prefixes(
    raw_segments: list[str],
    allow_prefixes: list[str] | None,
) -> None:
    # Empty list is an explicit deny-all policy; None means no allowlist was configured.
    if allow_prefixes is not None and len(allow_prefixes) == 0:
        raise ValueError("allow_prefixes is empty; no archive path allowed")
    if allow_prefixes and not _path_matches_allowed_prefix(raw_segments, allow_prefixes):
        raise path_not_allowed_error()


def _path_matches_allowed_prefix(raw_segments: list[str], allow_prefixes: list[str]) -> bool:
    allowed = [_validated_normalized_prefix(prefix) for prefix in allow_prefixes]
    # Compare allow-prefixes against raw Zammad path segments, not sanitized output, so
    # similarly sanitized but different source paths cannot bypass the configured policy.
    normalized_segments = [_normalize_prefix_segment(segment) for segment in raw_segments]
    return any(normalized_segments[: len(prefix)] == prefix for prefix in allowed)


def _validated_normalized_prefix(prefix: str) -> list[str]:
    prefix_parts = _parse_prefix_segments(prefix)
    validate_segments(prefix_parts)
    return [_normalize_prefix_segment(part) for part in prefix_parts]


def _render_filename_pattern(pattern: str, *, ticket_number: str, timestamp_utc: str) -> str:
    try:
        return pattern.format(
            ticket_number=ticket_number,
            timestamp_utc=timestamp_utc,
        )
    except KeyError as exc:
        raise invalid_filename_error(
            f"invalid filename_pattern format: unknown placeholder {exc.args[0]!r}"
        ) from exc
    except ValueError:
        # Re-raise ValueError as-is (e.g., from format specifier errors)
        raise
    except (IndexError, TypeError) as exc:  # positional/type errors in format string
        raise ValueError(f"invalid filename_pattern format: {exc}") from exc
