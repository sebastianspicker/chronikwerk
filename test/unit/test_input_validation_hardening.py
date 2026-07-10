"""Tests for hardened input validation across entry points."""

from __future__ import annotations

# Each test imports the private parser after supplying its synthetic request scope.
# pylint: disable=import-outside-toplevel
# ruff: noqa: I001  # Pylint and Ruff classify the in-repository test package differently.

import pytest
from pydantic import ValidationError

from zammad_pdf_archiver.app.routes.ingest import MAX_BATCH_SIZE, IngestPayload

# ---------------------------------------------------------------------------
# IngestPayload: ticket_id must be positive
# ---------------------------------------------------------------------------


def test_ingest_payload_rejects_zero_ticket_id() -> None:
    with pytest.raises(ValidationError):
        IngestPayload.model_validate({"ticket_id": 0})


def test_ingest_payload_rejects_negative_ticket_id() -> None:
    with pytest.raises(ValidationError):
        IngestPayload.model_validate({"ticket_id": -1})


def test_ingest_payload_accepts_positive_ticket_id() -> None:
    p = IngestPayload.model_validate({"ticket_id": 42})
    assert p.resolved_ticket_id() == 42


def test_ingest_payload_accepts_nested_ticket_id() -> None:
    p = IngestPayload.model_validate({"ticket": {"id": 7}})
    assert p.resolved_ticket_id() == 7


# ---------------------------------------------------------------------------
# MAX_BATCH_SIZE constant is defined and reasonable
# ---------------------------------------------------------------------------


def test_max_batch_size_is_positive() -> None:
    assert MAX_BATCH_SIZE > 0


def test_max_batch_size_has_upper_bound() -> None:
    # Sanity: batch limit should not be absurdly large.
    assert MAX_BATCH_SIZE <= 1000


# ---------------------------------------------------------------------------
# Rate limit: header fallback to scope-based key
# ---------------------------------------------------------------------------


def test_rate_limit_header_missing_falls_back_to_scope() -> None:
    from zammad_pdf_archiver.app.middleware.rate_limit import _client_key_from_header

    scope: dict[str, object] = {
        "headers": [],
        "client": ("192.168.1.1", 12345),
    }
    # When header is absent, should fall back to connection-level client address.
    key = _client_key_from_header(scope, "X-Forwarded-For")
    assert key == "192.168.1.1"


def test_rate_limit_header_empty_falls_back_to_scope() -> None:
    from zammad_pdf_archiver.app.middleware.rate_limit import _client_key_from_header

    scope: dict[str, object] = {
        "headers": [(b"x-forwarded-for", b"")],
        "client": ("10.0.0.1", 9999),
    }
    key = _client_key_from_header(scope, "X-Forwarded-For")
    assert key == "10.0.0.1"


def test_rate_limit_header_present_returns_header_value() -> None:
    from zammad_pdf_archiver.app.middleware.rate_limit import _client_key_from_header

    scope: dict[str, object] = {
        "headers": [(b"x-forwarded-for", b"1.2.3.4, 5.6.7.8")],
        "client": ("10.0.0.1", 9999),
    }
    key = _client_key_from_header(scope, "X-Forwarded-For")
    assert key == "1.2.3.4"
