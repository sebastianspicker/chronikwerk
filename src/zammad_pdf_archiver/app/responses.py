"""Centralized API response helpers for consistent JSON error and success shapes."""

from __future__ import annotations

import hashlib
import hmac

from fastapi import HTTPException, Request
from pydantic import SecretStr
from starlette.responses import JSONResponse

from zammad_pdf_archiver.config.settings import Settings


def settings_or_503(request: Request) -> Settings:
    """Extract Settings from app state or raise HTTP 503."""
    settings: Settings | None = getattr(request.app.state, "settings", None)
    if settings is None:
        raise HTTPException(status_code=503, detail="settings_not_configured")
    return settings


def bearer_auth_matches(request: Request, token: SecretStr) -> bool:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or len(auth) < 8:
        return False

    expected = token.get_secret_value().encode("utf-8")
    provided = auth[7:].strip().encode("utf-8")
    expected_hash = hashlib.sha256(expected).digest()
    provided_hash = hashlib.sha256(provided).digest()
    return hmac.compare_digest(expected_hash, provided_hash)


def verify_bearer_token(
    request: Request,
    token: SecretStr | None,
    *,
    missing_detail: str,
) -> None:
    if token is None or not token.get_secret_value():
        raise HTTPException(status_code=503, detail=missing_detail)
    if not bearer_auth_matches(request, token):
        raise HTTPException(status_code=401, detail="unauthorized")


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
