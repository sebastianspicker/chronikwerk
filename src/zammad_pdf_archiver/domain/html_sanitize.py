from __future__ import annotations

from html import escape
from html.parser import HTMLParser
from typing import Final

from zammad_pdf_archiver.domain.html_sanitize_attrs import clean_attrs, rendered_attrs
from zammad_pdf_archiver.domain.html_sanitize_stack import (
    close_matching_open_tag,
    pop_skip_stack_for_tag,
)

_ALLOWED_TAGS: Final[frozenset[str]] = frozenset(
    {
        "a",
        "b",
        "blockquote",
        "br",
        "code",
        "div",
        "em",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "i",
        "li",
        "ol",
        "p",
        "pre",
        "span",
        "strong",
        "table",
        "tbody",
        "td",
        "th",
        "thead",
        "tr",
        "u",
        "ul",
    }
)

_DROP_WITH_CONTENT: Final[frozenset[str]] = frozenset(
    {
        "script",
        "style",
        "iframe",
        "object",
        "embed",
        "link",
        "meta",
        "base",
        "form",
        "input",
        "button",
        "textarea",
        "select",
        "option",
    }
)

_VOID_TAGS: Final[frozenset[str]] = frozenset({"br", "hr"})

class _AllowlistHTMLSanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []
        self._open: list[str] = []
        self._skip_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self._mark_skip_depth(tag):
            return
        if self._skip_stack:
            return

        if not self._is_allowed_tag(tag):
            return

        attr_text = rendered_attrs(clean_attrs(tag, attrs))
        if tag in _VOID_TAGS:
            self._out.append(f"<{tag}{attr_text} />")
            return

        self._out.append(f"<{tag}{attr_text}>")
        self._open.append(tag)

    def _mark_skip_depth(self, tag: str) -> bool:
        if tag in _DROP_WITH_CONTENT:
            self._skip_stack.append(tag)
            return True
        return False

    def _is_allowed_tag(self, tag: str) -> bool:
        # Keep malformed or intentionally deep fragments from creating excessive parser output.
        return tag in _ALLOWED_TAGS and len(self._open) < 50

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # Normalize <br/> style tags; route through the same allowlist logic.
        self.handle_starttag(tag, attrs)

    def _pop_skip_stack_for_tag(self, tag: str) -> bool:
        return pop_skip_stack_for_tag(
            self._skip_stack,
            tag=tag,
            drop_with_content=_DROP_WITH_CONTENT,
        )

    def _close_matching_open_tag(self, tag: str) -> bool:
        return close_matching_open_tag(self._open, self._out, tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._pop_skip_stack_for_tag(tag):
            return
        if self._skip_stack:
            return
        if tag in _VOID_TAGS:
            return
        if self._close_matching_open_tag(tag):
            return
        # No matching open tag found — discard the end tag silently.

    def handle_data(self, data: str) -> None:
        if self._skip_stack:
            return
        if data:
            self._out.append(escape(data))

    def close(self) -> None:
        super().close()
        # Close any still-open tags to keep output well-formed.
        while self._open:
            tag = self._open.pop()
            self._out.append(f"</{tag}>")

    def sanitized_html(self) -> str:
        return "".join(self._out).strip()


def sanitize_html_fragment(html: str) -> str:
    """
    Sanitize an HTML fragment using a strict allowlist.

    Security goals (minimum per docs/05-pdf-rendering.md):
      - Drop <script>/<style> and similar active content.
      - Remove event-handler attributes (onclick, ...).
      - Neutralize dangerous URL schemes (javascript:, data:, file:, ...).

    This is intended for rendering ticket content into PDFs (print output), not
    for general-purpose HTML or browser UI. It is a local allowlist sanitizer,
    not an externally audited replacement for a dedicated HTML sanitization
    library.
    """
    if not isinstance(html, str) or not html:
        return ""

    try:
        parser = _AllowlistHTMLSanitizer()
        parser.feed(html)
        parser.close()
        return parser.sanitized_html()
    except Exception:  # noqa: BLE001 -- fail-closed: malformed HTML must not crash the sanitizer
        # Return empty so callers can fall back to rendering body_text.
        return ""
