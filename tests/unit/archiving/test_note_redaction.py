"""Verify archive error notes redact credential shapes the old note scrubber missed."""

from __future__ import annotations

import pytest

from chronikwerk.archiving.notes import concise_exc_message


@pytest.mark.parametrize(
    ("message", "secret"),
    [
        ("failed key_password=hunter2", "hunter2"),
        ('failed {"token": "abc def"}', "abc def"),
        ("failed\nZAMMAD__API_TOKEN=abcsecretvalue", "abcsecretvalue"),
        ("failed password: 'two words here'", "words here"),
    ],
)
def test_error_note_text_redacts_secrets(message: str, secret: str) -> None:
    """Error-note text must not disclose secrets in any supported credential shape."""
    assert secret not in concise_exc_message(RuntimeError(message))
