"""Centralized API response helpers for consistent JSON error and success shapes."""

from __future__ import annotations

from fastapi import HTTPException, Request
from starlette.responses import JSONResponse

from zammad_pdf_archiver.config.settings import Settings


def settings_or_503(request: Request) -> Settings:
    """Extract Settings from app state or raise HTTP 503."""
    settings: Settings | None = getattr(request.app.state, "settings", None)
    if settings is None:
        raise HTTPException(status_code=503, detail="settings_not_configured")
    return settings


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
