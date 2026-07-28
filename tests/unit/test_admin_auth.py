"""Verifies admin session expiry and exact access-token comparison."""

from __future__ import annotations

import time

from pydantic import SecretStr

from chronikwerk.app.admin.auth import AdminSessionStore, access_token_matches


def test_session_store_enforces_idle_and_absolute_expiry() -> None:
    store = AdminSessionStore(idle_seconds=60, absolute_seconds=300)
    idle = store.create(locale="de_DE")
    assert idle.locale == "de-DE"
    idle.last_seen_at = time.time() - 61
    assert store.get(idle.session_id) is None

    absolute = store.create(locale="en_GB")
    absolute.created_at = time.time() - 301
    assert store.get(absolute.session_id) is None


def test_access_token_comparison_is_exact() -> None:
    token = SecretStr("a" * 32)
    assert access_token_matches("a" * 32, token) is True
    assert access_token_matches("a" * 31, token) is False
    assert access_token_matches("", None) is False
