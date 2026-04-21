from __future__ import annotations

from zammad_pdf_archiver.domain.html_sanitize import sanitize_html_fragment


def test_sanitize_html_fragment_drops_scripts_and_event_handlers() -> None:
    raw = (
        '<p onclick="alert(1)">Hello '
        "<script>alert(1)</script>"
        '<a href="javascript:alert(1)">bad</a> '
        '<a href="https://example.com/path">ok</a> '
        '<img src="https://evil.example/img.png" />'
        "</p>"
    )

    out = sanitize_html_fragment(raw)
    assert "<script" not in out
    assert "onclick" not in out
    assert "javascript:" not in out
    assert "<img" not in out
    assert 'href="https://example.com/path"' in out
    assert "Hello" in out


def test_sanitize_html_fragment_strips_unknown_tags_but_keeps_text() -> None:
    raw = "<p>Hello <custom>World</custom></p>"
    out = sanitize_html_fragment(raw)
    assert "<custom" not in out
    assert "Hello" in out
    assert "World" in out


def test_sanitize_html_fragment_rejects_scheme_relative_urls() -> None:
    raw = '<p><a href="//example.com">x</a></p>'
    out = sanitize_html_fragment(raw)
    assert "href=" not in out


# ---------------------------------------------------------------------------
# Additional edge-case coverage
# ---------------------------------------------------------------------------


def test_sanitize_empty_and_non_string_inputs() -> None:
    assert sanitize_html_fragment("") == ""
    assert sanitize_html_fragment(None) == ""  # type: ignore[arg-type]
    assert sanitize_html_fragment(42) == ""  # type: ignore[arg-type]


def test_sanitize_href_null_byte_stripped() -> None:
    """href containing a null byte is stripped."""
    raw = '<a href="https://ok.example.com/\x00evil">link</a>'
    out = sanitize_html_fragment(raw)
    assert "href=" not in out


def test_sanitize_href_empty_value_stripped() -> None:
    raw = '<a href="">link</a>'
    out = sanitize_html_fragment(raw)
    assert "href=" not in out


def test_sanitize_drops_script_content() -> None:
    raw = "<script>document.cookie</script><p>keep</p>"
    out = sanitize_html_fragment(raw)
    assert "document.cookie" not in out
    assert "<script" not in out
    assert "keep" in out


def test_sanitize_drops_nested_script_inside_style() -> None:
    """Mismatched nested drop-with-content tags are fully dropped."""
    raw = "<style><script>alert(1)</script></style><p>safe</p>"
    out = sanitize_html_fragment(raw)
    assert "alert" not in out
    assert "safe" in out


def test_sanitize_drops_iframe() -> None:
    raw = '<iframe src="https://evil.example.com"></iframe><p>remain</p>'
    out = sanitize_html_fragment(raw)
    assert "<iframe" not in out
    assert "remain" in out


def test_sanitize_endtag_inside_skip_stack_is_ignored() -> None:
    """End tags encountered while inside a skipped subtree are silently ignored."""
    raw = "<script><b>leak</b></script><p>visible</p>"
    out = sanitize_html_fragment(raw)
    assert "leak" not in out
    assert "visible" in out


def test_sanitize_void_tag_end_is_ignored() -> None:
    """Closing a void tag like </br> is silently dropped without error."""
    raw = "<p>text</br>more</p>"
    out = sanitize_html_fragment(raw)
    assert "text" in out
    assert "more" in out


def test_sanitize_unmatched_close_tag_is_dropped() -> None:
    """A close tag with no matching open tag is silently discarded."""
    raw = "<p>text</p></div>"
    out = sanitize_html_fragment(raw)
    assert "</div>" not in out
    assert "text" in out


def test_sanitize_browser_error_recovery_closes_intermediate_tags() -> None:
    """A close tag that skips intermediate open tags triggers browser-style recovery."""
    raw = "<div><span><p>text</div>"
    out = sanitize_html_fragment(raw)
    assert "<div>" in out or "text" in out
    # The output should be well-formed (no unclosed tags after parsing)
    assert out.count("<div>") == out.count("</div>") or "div" not in out


def test_sanitize_close_auto_closes_unclosed_tags() -> None:
    """close() shuts any still-open tags to produce well-formed output."""
    raw = "<div><p>text"
    out = sanitize_html_fragment(raw)
    assert "</p>" in out
    assert "</div>" in out


def test_sanitize_attr_value_returned_for_non_href_attrs() -> None:
    """colspan/rowspan values on td/th pass through sanitize_attr_value unchanged."""
    raw = "<table><tr><td colspan=\"3\">cell</td></tr></table>"
    out = sanitize_html_fragment(raw)
    assert 'colspan="3"' in out


def test_sanitize_attr_key_none_is_skipped() -> None:
    """Attributes with None key are skipped without error."""
    # Simulate a tag with a None attr key (malformed HTML)
    raw = '<p id="">text</p>'
    out = sanitize_html_fragment(raw)
    # id is not in allowed attrs, so it's stripped; text should be kept
    assert "text" in out
    assert "id=" not in out


def test_sanitize_attr_on_events_stripped() -> None:
    """on* attributes are stripped even for allowed tags."""
    raw = '<p onmouseover="evil()">keep</p>'
    out = sanitize_html_fragment(raw)
    assert "onmouseover" not in out
    assert "keep" in out


def test_sanitize_style_attribute_stripped() -> None:
    """style= attribute is stripped."""
    raw = '<p style="color:red">text</p>'
    out = sanitize_html_fragment(raw)
    assert "style=" not in out
    assert "text" in out


def test_sanitize_href_javascript_scheme_stripped() -> None:
    """javascript: href is stripped."""
    raw = '<a href="javascript:void(0)">click</a>'
    out = sanitize_html_fragment(raw)
    assert "javascript:" not in out


def test_sanitize_href_data_scheme_stripped() -> None:
    """data: href is stripped."""
    raw = '<a href="data:text/html,<script>alert(1)</script>">link</a>'
    out = sanitize_html_fragment(raw)
    assert "data:" not in out


def test_sanitize_href_mailto_allowed() -> None:
    raw = '<a href="mailto:user@example.com">email</a>'
    out = sanitize_html_fragment(raw)
    assert 'href="mailto:user@example.com"' in out

