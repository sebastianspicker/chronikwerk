from __future__ import annotations

from fastapi import APIRouter, Request
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

from zammad_pdf_archiver.app.responses import bearer_auth_matches

router = APIRouter()


@router.get("/metrics")
def metrics(request: Request) -> Response:
    settings = getattr(request.app.state, "settings", None)
    if settings is not None:
        token = settings.observability.metrics_bearer_token
        if token is not None and not bearer_auth_matches(request, token):
            return Response(
                content="Unauthorized\n",
                status_code=401,
                media_type="text/plain",
            )
    return Response(content=generate_latest(), headers={"Content-Type": CONTENT_TYPE_LATEST})
