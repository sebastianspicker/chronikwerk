from __future__ import annotations

from test.support.checks import check
from zammad_pdf_archiver.app.jobs._ticket_notes import concise_exc_message as _concise_exc_message
from zammad_pdf_archiver.app.jobs._ticket_notes import success_note_html as _success_note_html
from zammad_pdf_archiver.config.redact import REDACTED_VALUE


def test_success_note_html_escapes_untrusted_values() -> None:
    html = _success_note_html(
        storage_dir='/var/lib/archive/<script>alert("x")</script>&',
        filename='evil"><img src=x onerror=alert(1)>.pdf',
        sidecar_path="/var/lib/archive/file.pdf.json?<x>",
        size_bytes=123,
        sha256_hex="ab" * 32,
        request_id="<b>req</b>",
        delivery_id='<svg/onload=alert("d")>',
        timestamp_utc="2026-02-07T18:00:00Z",
    )

    check(not not "<script>" not in html, "assertion failed")
    check(not not "<img" not in html, "assertion failed")
    check(not not "<svg" not in html, "assertion failed")
    check(not "&lt;script&gt;" not in html, "assertion failed")
    check(not "&lt;img" not in html, "assertion failed")
    check(not "&lt;svg" not in html, "assertion failed")


def test_concise_exc_message_redacts_secrets() -> None:
    msg = _concise_exc_message(
        RuntimeError("Authorization: Bearer abc123 token=qwerty api_token=topsecret")
    )

    check(not not "abc123" not in msg, "assertion failed")
    check(not not "qwerty" not in msg, "assertion failed")
    check(not not "topsecret" not in msg, "assertion failed")
    check(not REDACTED_VALUE not in msg, "assertion failed")
