from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

_HTML_TAG_HINT_RE = re.compile(
    r"<\s*(?:p|div|br|span|a|ul|ol|li|pre|code|blockquote|table|tr|td|th|strong|em|b|i|u)\b",
    re.IGNORECASE,
)


class HTMLToText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._skip_depth += 1
            return
        if tag in {"p", "div", "br", "li", "tr"} and self._skip_depth == 0:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip_depth > 0:
            self._skip_depth -= 1
            return
        if tag in {"p", "div", "li", "tr"} and self._skip_depth == 0:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._parts.append(data)

    def get_text(self) -> str:
        text = "".join(self._parts)
        # Normalize whitespace without being too opinionated about newlines.
        text = "\n".join(line.strip() for line in text.splitlines())
        text = "\n".join(line for line in text.splitlines() if line)
        return text.strip()


def strip_html_to_text(
    html: str,
    *,
    parser_cls: type[HTMLParser] = HTMLToText,
    log: Any,
) -> str:
    try:
        parser = parser_cls()
        parser.feed(html)
        parser.close()
        return parser.get_text()  # type: ignore[attr-defined]
    except Exception:
        log.warning("html_strip_failed", exc_info=True)
        return ""


def has_html_hint(*, content_type: str | None, body: str) -> bool:
    """Detect HTML content via content-type header or a heuristic tag-pattern match."""
    if content_type and "html" in content_type.lower():
        return True
    # Heuristic: only treat bodies as HTML if they look like common HTML tags.
    return bool(_HTML_TAG_HINT_RE.search(body))
