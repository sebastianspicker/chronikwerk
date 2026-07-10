"""Project module."""
from __future__ import annotations

from pathlib import Path

from zammad_pdf_archiver.domain.path_policy import (
    ensure_within_root,
    sanitize_segment,
    validate_segments,
)


def _target_from_segments(root: Path, user_safe: str, segs_safe: list[str]) -> Path:
    target = root / user_safe
    for seg in segs_safe:
        target = target / seg
    return target


def build_target_dir(
    root: Path,
    username: str,
    segments: list[str] | tuple[str, ...],
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

    target = _target_from_segments(root, user_safe, segs_safe)
    ensure_within_root(root, target)
    return target


def _render_filename_pattern(pattern: str, *, ticket_safe: str, ts_safe: str) -> str:
    try:
        return pattern.format(
            ticket_number=ticket_safe,
            timestamp_utc=ts_safe,
            date_utc=ts_safe,
        )
    except KeyError as exc:
        raise ValueError(
            f"invalid filename_pattern format: unknown placeholder {exc.args[0]!r}"
        ) from exc
    except (IndexError, TypeError) as exc:
        raise ValueError(f"invalid filename_pattern format: {exc}") from exc


def _validate_rendered_filename(rendered: str) -> str:
    rendered = rendered.strip()
    if not rendered:
        raise ValueError("filename_pattern produced an empty filename")
    if rendered in (".", ".."):
        raise ValueError("filename must not be '.' or '..'")
    if "/" in rendered or "\\" in rendered or "\x00" in rendered:
        raise ValueError("filename_pattern must not include path separators or null bytes")
    validate_segments([rendered], max_depth=1, max_length=255)
    return rendered


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
      - {date_utc}      (alias for {timestamp_utc})

    The rendered filename is validated to be a single safe path segment.
    """
    if not isinstance(pattern, str) or not pattern.strip():
        raise ValueError("pattern must be a non-empty string")

    ticket_safe = sanitize_segment(str(ticket_number))
    ts_safe = sanitize_segment(timestamp_utc)

    rendered = _render_filename_pattern(pattern, ticket_safe=ticket_safe, ts_safe=ts_safe)
    return _validate_rendered_filename(rendered)
