from __future__ import annotations

import re
import unicodedata

from zammad_pdf_archiver.domain.path_policy import validate_segments

_PREFIX_SPLIT_RE = re.compile(r"[>/]")


def path_matches_allowed_prefix(raw_segments: list[str], allow_prefixes: list[str]) -> bool:
    allowed = [_validated_normalized_prefix(prefix) for prefix in allow_prefixes]
    # Compare allow-prefixes against raw Zammad path segments, not sanitized output, so
    # similarly sanitized but different source paths cannot bypass the configured policy.
    normalized_segments = [_normalize_prefix_segment(segment) for segment in raw_segments]
    return any(normalized_segments[: len(prefix)] == prefix for prefix in allowed)


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


def _validated_normalized_prefix(prefix: str) -> list[str]:
    prefix_parts = _parse_prefix_segments(prefix)
    validate_segments(prefix_parts)
    return [_normalize_prefix_segment(part) for part in prefix_parts]
