"""Centralized web response helpers for consistent JSON error shapes."""

from __future__ import annotations

import hashlib
import hmac

from fastapi import HTTPException, Request
from pydantic import SecretStr
from starlette.responses import JSONResponse


def secret_matches(provided: str, expected: SecretStr) -> bool:
    """Compare a presented secret with the expected one in constant time."""
    provided_hash = hashlib.sha256(provided.encode("utf-8")).digest()
    expected_hash = hashlib.sha256(expected.get_secret_value().encode("utf-8")).digest()
    return hmac.compare_digest(provided_hash, expected_hash)


def bearer_auth_matches(request: Request, token: SecretStr) -> bool:
    """Compare a bearer authorization header in constant time when configured."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or len(auth) < 8:
        return False

    return secret_matches(auth[7:].strip(), token)


def verify_bearer_token(
    request: Request,
    token: SecretStr | None,
    *,
    missing_detail: str,
) -> None:
    """Reject requests that do not present the configured bearer token."""
    if token is None or not token.get_secret_value().strip():
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
