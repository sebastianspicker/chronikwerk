from __future__ import annotations

from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from zammad_pdf_archiver.adapters.http_util import drain_stream
from zammad_pdf_archiver.app.responses import api_error


def forbidden() -> JSONResponse:
    return api_error(403, "forbidden", code="forbidden")


def service_misconfigured() -> JSONResponse:
    # Fail closed: running without webhook auth is almost always a production footgun.
    return api_error(503, "webhook_auth_not_configured", code="webhook_auth_not_configured")


def missing_delivery_id() -> JSONResponse:
    return api_error(400, "missing_delivery_id", code="missing_delivery_id")


async def send_json_response(
    response: JSONResponse, scope: Scope, receive: Receive, send: Send
) -> None:
    await response(scope, receive, send)


async def drain_and_send(
    response: JSONResponse, scope: Scope, receive: Receive, send: Send
) -> None:
    await drain_stream(receive)
    await send_json_response(response, scope, receive, send)
