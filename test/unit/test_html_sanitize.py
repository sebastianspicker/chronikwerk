from __future__ import annotations

from zammad_pdf_archiver.domain.html_sanitize import sanitize_html_fragment


def test_sanitizer_preserves_rich_formatting_tables_and_safe_links() -> None:
    value = sanitize_html_fragment(
        '<p><strong>Report</strong></p><table><tr><td colspan="2">Total</td></tr>'
        '</table><a href="https://example.invalid" onclick="evil()">open</a>'
    )

    assert "<strong>Report</strong>" in value
    assert "<table>" in value and "colspan=\"2\"" in value
    assert 'href="https://example.invalid"' in value
    assert "onclick" not in value


def test_sanitizer_drops_active_content_and_dangerous_urls() -> None:
    value = sanitize_html_fragment(
        '<script>alert(1)</script><style>x{}</style><form><input value="x"></form>'
        '<a href="javascript:alert(1)">bad</a><a href="//evil.invalid">also bad</a>'
        '<a href="mailto:user@example.invalid">good</a>'
    )

    assert "alert" not in value
    assert "style" not in value
    assert "form" not in value
    assert "javascript" not in value
    assert "//evil.invalid" not in value
    assert 'href="mailto:user@example.invalid"' in value


def test_sanitizer_recovers_malformed_nesting_and_entities() -> None:
    value = sanitize_html_fragment("<p>one &amp; <b>two</p><div>three")

    assert value == "<p>one &amp; <b>two</b></p><div>three</div>"
