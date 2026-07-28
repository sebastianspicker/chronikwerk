"""Verifies admin response helpers enforce configuration and bearer-token failures."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import Request
from starlette.datastructures import State

from chronikwerk.app.responses import api_error, settings_or_503, verify_bearer_token
from chronikwerk.config.settings import Settings
from tests.support.settings_factory import make_settings


def _make_request(state_settings: Settings | None = None, include_settings: bool = True) -> Request:
    """Build a Starlette Request with app.state.settings set."""
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
    """Build the authed request fixture used by this scenario."""
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
    assert settings_or_503(req) is s


def test_settings_or_503_raises_503_when_none() -> None:
    req = _make_request(state_settings=None)
    with pytest.raises(Exception) as exc_info:
        settings_or_503(req)
    assert exc_info.value.status_code == 503  # type: ignore[attr-defined]


def test_settings_or_503_raises_503_when_no_state_attr() -> None:
    """If app.state has no settings attribute at all, raise 503."""
    req = _make_request(include_settings=False)
    with pytest.raises(Exception) as exc_info:
        settings_or_503(req)
    assert exc_info.value.status_code == 503  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# verify_bearer_token
# ---------------------------------------------------------------------------


def test_verify_bearer_token_raises_503_when_no_token_configured(tmp_path) -> None:
    s = make_settings(str(tmp_path))
    req = _make_authed_request("any-token")

    with pytest.raises(Exception) as exc_info:
        verify_bearer_token(req, s.retry_bearer_token, missing_detail="retry_token_not_configured")
    assert exc_info.value.status_code == 503  # type: ignore[attr-defined]


def test_verify_bearer_token_raises_401_for_wrong_token(tmp_path) -> None:
    s = make_settings(str(tmp_path), overrides={"retry_bearer_token": "correct-token"})
    req = _make_authed_request("wrong-token")

    with pytest.raises(Exception) as exc_info:
        verify_bearer_token(req, s.retry_bearer_token, missing_detail="retry_token_not_configured")
    assert exc_info.value.status_code == 401  # type: ignore[attr-defined]


def test_verify_bearer_token_succeeds_for_correct_token(tmp_path) -> None:
    s = make_settings(str(tmp_path), overrides={"retry_bearer_token": "my-secret"})
    req = _make_authed_request("my-secret")

    # Should not raise
    verify_bearer_token(req, s.retry_bearer_token, missing_detail="retry_token_not_configured")


# ---------------------------------------------------------------------------
# api_error
# ---------------------------------------------------------------------------


def test_api_error_basic() -> None:
    resp = api_error(400, "Bad input")
    assert resp.status_code == 400


def test_api_error_with_hint() -> None:
    resp = api_error(400, "Bad input", hint="Check the payload")
    import json

    body = json.loads(bytes(resp.body))
    assert "Check the payload" in body.get("hint", "")


def test_api_error_with_request_id() -> None:
    resp = api_error(422, "Validation failed", request_id="req-abc")
    import json

    body = json.loads(bytes(resp.body))
    assert body.get("request_id") == "req-abc"


def test_api_error_with_custom_headers() -> None:
    resp = api_error(401, "Unauthorized", headers={"WWW-Authenticate": "Bearer"})
    assert resp.headers.get("WWW-Authenticate") == "Bearer"
