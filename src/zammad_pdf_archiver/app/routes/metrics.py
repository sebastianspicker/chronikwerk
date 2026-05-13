from __future__ import annotations

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse, Response

from zammad_pdf_archiver.app.responses import constant_time_token_match
from zammad_pdf_archiver.observability.metrics import render_latest

router = APIRouter()


def _metrics_unauthorized() -> Response:
    return Response(
        content="Unauthorized\n",
        status_code=401,
        media_type="text/plain",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _metrics_token_not_configured() -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "detail": "metrics_token_not_configured",
            "code": "metrics_token_not_configured",
        },
    )


@router.get("/metrics")
def metrics(request: Request) -> Response:
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        return _metrics_token_not_configured()

    token = settings.observability.metrics_bearer_token
    expected = token.get_secret_value().encode("utf-8") if token is not None else b""
    if not expected:
        return _metrics_token_not_configured()

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or len(auth) < 8:
        return _metrics_unauthorized()
    provided = auth[7:].strip().encode("utf-8")
    if not constant_time_token_match(expected, provided):
        return _metrics_unauthorized()
    payload, content_type = render_latest()
    return Response(content=payload, headers={"Content-Type": content_type})
