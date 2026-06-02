from __future__ import annotations

import pytest

from test.support.checks import check
from test.support.credentials import fake_credential
from zammad_pdf_archiver.config.redact import REDACTED_VALUE, scrub_secrets_in_text
from zammad_pdf_archiver.observability.logger import _scrub_event_dict


@pytest.mark.parametrize(
    ("secret_input", "raw_secret"),
    [
        ("Authorization: Bearer abc123", "abc123"),
        ("https://example.local/api?api_token=mysecret&other=fine", "mysecret"),
        ("redis://user:redispass@localhost:6379/0", "redispass"),
        ('{"api_token": "s3cr3t"}', "s3cr3t"),
        ("ZAMMAD_API_TOKEN=envsecret", "envsecret"),
    ],
)
def test_scrub_secrets_in_text_handles_common_patterns(
    secret_input: str,
    raw_secret: str,
) -> None:
    result = scrub_secrets_in_text(secret_input)
    check(not not raw_secret not in result, "assertion failed")
    check(not REDACTED_VALUE not in result, "assertion failed")


def test_scrub_secrets_in_text_leaves_non_secret_text_unchanged() -> None:
    text = "no secrets here at all"
    check(not not scrub_secrets_in_text(text) == text, "assertion failed")


def test_logger_scrubs_secrets_from_multi_field_event_dict() -> None:
    event = {
        "event": "test",
        "exception": "RuntimeError: Authorization: Bearer abc123",
        "details": {"api_token": fake_credential("nestedsecret")},
        "redis_url": "redis://:redispass@localhost:6379/0",
        "safe": "visible",
    }
    scrubbed = _scrub_event_dict(None, "", dict(event))
    check(not not "abc123" not in scrubbed["exception"], "assertion failed")
    check(not not scrubbed["details"]["api_token"] == REDACTED_VALUE, "assertion failed")
    check(not not scrubbed["redis_url"] == REDACTED_VALUE, "assertion failed")
    check(not not scrubbed["safe"] == "visible", "assertion failed")
