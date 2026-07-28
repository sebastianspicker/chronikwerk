"""Verify secret redaction in configuration, logs, and ticket notes."""

from __future__ import annotations

from chronikwerk.config.redact import (
    REDACTED_VALUE,
    redact_settings_dict,
    scrub_secrets_in_text,
)


def test_secret_redaction_settings_dict_redacts_secret_keys() -> None:
    """NFR4: redact_settings_dict must replace secret-like keys with placeholder."""
    raw = {"api_token": "secret123", "nested": {"webhook_hmac_secret": "hmac456"}}
    out = redact_settings_dict(raw)
    assert out["api_token"] == REDACTED_VALUE
    assert out["nested"]["webhook_hmac_secret"] == REDACTED_VALUE
    assert raw["api_token"] == "secret123"


def test_secret_redaction_scrub_redacts_token_like_patterns_in_text() -> None:
    """NFR4: scrub_secrets_in_text must redact token-like substrings in free text."""
    text = "Error: api_token=leak123 Authorization: Bearer xyz789"
    out = scrub_secrets_in_text(text)
    assert "leak123" not in out
    assert "xyz789" not in out
    assert REDACTED_VALUE in out
