"""Verifies log exception text is scrubbed of credential material."""

from __future__ import annotations

from chronikwerk.observability.logger import _scrub_event_dict


def test_logger_scrubs_secrets_from_exception_strings() -> None:
    event = {
        "event": "test",
        "exception": "RuntimeError: Authorization: Bearer abc123",
    }
    scrubbed = _scrub_event_dict(None, "", dict(event))
    assert "abc123" not in scrubbed["exception"]
