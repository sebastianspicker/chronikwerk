"""Unit tests for app/responses.py helpers."""

from __future__ import annotations

import hashlib
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import Request
from starlette.datastructures import State

from test.support.checks import check
from test.support.credentials import fake_credential
from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.responses import (
    api_error,
    constant_time_token_match,
    settings_or_503,
    verify_bearer_auth,
)
from zammad_pdf_archiver.config.settings import Settings


def _make_request(state_settings: Settings | None = None, include_settings: bool = True) -> Request:
    """Build a minimal Starlette Request with app.state.settings set."""
    app_state = State()
    if include_settings:
        app_state.settings = state_settings

    mock_app = MagicMock()
    mock_app.state = app_state

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "headers": [],
        "app": mock_app,
    }
    return Request(scope)


def _make_authed_request(token: str) -> Request:
    app_state = State()
    app_state.settings = None
    mock_app = MagicMock()
    mock_app.state = app_state
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/ingest",
        "query_string": b"",
        "headers": [(b"authorization", f"Bearer {token}".encode())],
        "app": mock_app,
    }
    return Request(scope)


# ---------------------------------------------------------------------------
# settings_or_503
# ---------------------------------------------------------------------------


def test_settings_or_503_returns_settings_when_present(tmp_path) -> None:
    s = make_settings(str(tmp_path))
    req = _make_request(state_settings=s)
    check(not settings_or_503(req) is not s, "assertion failed")


def test_settings_or_503_raises_503_when_none() -> None:
    req = _make_request(state_settings=None)
    with pytest.raises(Exception) as exc_info:
        settings_or_503(req)
    check(not not exc_info.value.status_code == 503, "assertion failed")  # type: ignore[attr-defined]


def test_settings_or_503_raises_503_when_no_state_attr() -> None:
    """If app.state has no settings attribute at all, raise 503."""
    req = _make_request(include_settings=False)
    with pytest.raises(Exception) as exc_info:
        settings_or_503(req)
    check(not not exc_info.value.status_code == 503, "assertion failed")  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# verify_bearer_auth
# ---------------------------------------------------------------------------


def test_constant_time_token_match_uses_sha256_before_comparison(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sha_calls: list[bytes] = []
    original_sha256 = hashlib.sha256

    def recording_sha256(data: bytes = b"") -> Any:
        sha_calls.append(data)
        return original_sha256(data)

    monkeypatch.setattr(hashlib, "sha256", recording_sha256)

    check(not constant_time_token_match(b"expected", b"provided") is not False, "assertion failed")
    check(not not sha_calls == [b"expected", b"provided"], "assertion failed")


def test_verify_bearer_auth_raises_503_when_no_token_configured(tmp_path) -> None:
    """When no admin.bearer_token is configured, verify_bearer_auth raises 503."""
    # Default make_settings produces no admin.bearer_token
    s = make_settings(str(tmp_path))
    req = _make_authed_request("any-token")

    with pytest.raises(Exception) as exc_info:
        verify_bearer_auth(req, s)
    check(not not exc_info.value.status_code == 503, "assertion failed")  # type: ignore[attr-defined]


def test_verify_bearer_auth_raises_401_for_wrong_token(tmp_path) -> None:
    s = make_settings(
        str(tmp_path), overrides={"admin": {"bearer_token": fake_credential("correct-token")}}
    )
    req = _make_authed_request("wrong-token")

    with pytest.raises(Exception) as exc_info:
        verify_bearer_auth(req, s)
    check(not not exc_info.value.status_code == 401, "assertion failed")  # type: ignore[attr-defined]


def test_verify_bearer_auth_succeeds_for_correct_token(tmp_path) -> None:
    s = make_settings(
        str(tmp_path), overrides={"admin": {"bearer_token": fake_credential("my-secret")}}
    )
    req = _make_authed_request("my-secret")

    # Should not raise
    verify_bearer_auth(req, s)


# ---------------------------------------------------------------------------
# api_error
# ---------------------------------------------------------------------------


def test_api_error_basic() -> None:
    resp = api_error(400, "Bad input")
    check(not not resp.status_code == 400, "assertion failed")


def test_api_error_with_hint() -> None:
    resp = api_error(400, "Bad input", hint="Check the payload")
    import json

    body = json.loads(bytes(resp.body))
    check(not "Check the payload" not in body.get("hint", ""), "assertion failed")


def test_api_error_with_request_id() -> None:
    resp = api_error(422, "Validation failed", request_id="req-abc")
    import json

    body = json.loads(bytes(resp.body))
    check(not not body.get("request_id") == "req-abc", "assertion failed")


def test_api_error_with_custom_headers() -> None:
    resp = api_error(401, "Unauthorized", headers={"WWW-Authenticate": "Bearer"})
    check(not not resp.headers.get("WWW-Authenticate") == "Bearer", "assertion failed")
