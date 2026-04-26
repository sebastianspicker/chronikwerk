"""Centralized API response helpers for consistent JSON error and success shapes."""

from __future__ import annotations

import hashlib
import hmac

from fastapi import HTTPException, Request
from starlette.responses import JSONResponse

from zammad_pdf_archiver.config.settings import Settings


def settings_or_503(request: Request) -> Settings:
    """Extract Settings from app state or raise HTTP 503."""
    settings: Settings | None = getattr(request.app.state, "settings", None)
    if settings is None:
        raise HTTPException(status_code=503, detail="settings_not_configured")
    return settings


def verify_bearer_auth(
    request: Request,
    settings: Settings,
    *,
    missing_token_detail: str = "admin_token_not_configured",
) -> None:
    """Verify ``Authorization: Bearer <token>`` against the admin bearer token.

    Raises :class:`~fastapi.HTTPException` (401) when the token is missing or
    invalid, or (503) when no token has been configured.
    """
    token = settings.admin.bearer_token
    expected = token.get_secret_value().encode("utf-8") if token is not None else b""
    if not expected:
        raise HTTPException(status_code=503, detail=missing_token_detail)

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or len(auth) < 8:
        raise HTTPException(status_code=401, detail="unauthorized")

    provided = auth[7:].strip().encode("utf-8")
    # Hash both tokens with SHA-256 before comparing to normalise length and
    # prevent timing-based length leaks.
    expected_hash = hashlib.sha256(expected).digest()
    provided_hash = hashlib.sha256(provided).digest()
    if not hmac.compare_digest(expected_hash, provided_hash):
        raise HTTPException(status_code=401, detail="unauthorized")


def clamp_limit(value: int | None, *, default: int, minimum: int, maximum: int) -> int:
    """Clamp optional integer limits to a safe inclusive range."""
    resolved = default if value is None else int(value)
    return max(minimum, min(resolved, maximum))


def api_error(
    status_code: int,
    detail: str,
    *,
    code: str | None = None,
    hint: str | None = None,
    request_id: str | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Return a JSON error response with optional code and hint."""
    content: dict[str, str] = {"detail": detail}
    if code is not None:
        content["code"] = code
    if hint is not None:
        content["hint"] = hint
    if request_id is not None:
        content["request_id"] = request_id
    return JSONResponse(status_code=status_code, content=content, headers=headers)
