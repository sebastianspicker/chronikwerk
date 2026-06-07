from __future__ import annotations

from html import escape
from urllib.parse import urlparse

_ALLOWED_ATTRS: dict[str, frozenset[str]] = {
    "a": frozenset({"href", "title"}),
    "td": frozenset({"colspan", "rowspan"}),
    "th": frozenset({"colspan", "rowspan"}),
}

_ALLOWED_HREF_SCHEMES: frozenset[str] = frozenset({"", "http", "https", "mailto"})


def clean_attrs(tag: str, attrs: list[tuple[str, str | None]]) -> list[tuple[str, str]]:
    allowed_attrs = _ALLOWED_ATTRS.get(tag, frozenset())
    cleaned: list[tuple[str, str]] = []
    for key, value in attrs:
        normalized = normalized_attr_key(key)
        if normalized is None or value is None:
            continue
        if normalized not in allowed_attrs:
            continue
        sanitized = sanitize_attr_value(tag, normalized, value)
        if sanitized is None:
            continue
        cleaned.append((normalized, sanitized))
    return cleaned


def rendered_attrs(attrs: list[tuple[str, str]]) -> str:
    return "".join(f' {k}="{escape(v, quote=True)}"' for k, v in attrs)


def normalized_attr_key(key: str | None) -> str | None:
    if not key:
        return None
    key_norm = key.lower().strip()
    if not key_norm or key_norm.startswith("on") or key_norm == "style":
        return None
    return key_norm


def sanitize_attr_value(tag: str, key: str, value: str) -> str | None:
    if tag == "a" and key == "href":
        return sanitize_href(value)
    return value


def sanitize_href(raw: str) -> str | None:
    href = raw.strip()
    if not href or "\x00" in href:
        return None

    parsed = urlparse(href)
    scheme = (parsed.scheme or "").lower()

    # Disallow scheme-relative URLs like //example.com (netloc present, no scheme).
    if not scheme and parsed.netloc:
        return None

    if scheme not in _ALLOWED_HREF_SCHEMES:
        return None

    return href
