from __future__ import annotations

import base64
import binascii

from fastapi import HTTPException, Request

from zammad_pdf_archiver.app.responses import constant_time_token_match
from zammad_pdf_archiver.config.settings import Settings

_ADMIN_BASIC_CHALLENGE = 'Basic realm="zammad-pdf-archiver-admin"'


def dashboard_auth_error() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="unauthorized",
        headers={"WWW-Authenticate": _ADMIN_BASIC_CHALLENGE},
    )


def admin_token_bytes(settings: Settings) -> bytes:
    token = settings.admin.bearer_token
    expected = token.get_secret_value().encode("utf-8") if token is not None else b""
    if not expected:
        raise HTTPException(status_code=503, detail="admin_token_not_configured")
    return expected


def verify_admin_dashboard_auth(request: Request, settings: Settings) -> None:
    if not settings.admin.enabled:
        raise HTTPException(status_code=404, detail="admin_disabled")

    expected = admin_token_bytes(settings)
    auth = request.headers.get("Authorization", "")
    if dashboard_bearer_auth_ok(auth, expected):
        return
    if dashboard_basic_auth_ok(auth, expected):
        return
    raise dashboard_auth_error()


def dashboard_bearer_auth_ok(auth: str, expected: bytes) -> bool:
    if not auth.startswith("Bearer ") or len(auth) < 8:
        return False
    if constant_time_token_match(expected, auth[7:].strip().encode("utf-8")):
        return True
    raise dashboard_auth_error()


def dashboard_basic_auth_ok(auth: str, expected: bytes) -> bool:
    if not auth.startswith("Basic ") or len(auth) < 7:
        return False
    try:
        decoded = base64.b64decode(auth[6:].strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        raise dashboard_auth_error() from None

    _, separator, password = decoded.partition(":")
    return bool(separator and constant_time_token_match(expected, password.encode("utf-8")))
