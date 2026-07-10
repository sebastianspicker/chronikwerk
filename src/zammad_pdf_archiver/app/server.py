"""Project module."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from starlette.responses import Response

from zammad_pdf_archiver._version import __version__
from zammad_pdf_archiver.app.jobs.admission import JobAdmission
from zammad_pdf_archiver.app.jobs.shutdown import (
    clear_shutting_down,
    set_shutting_down,
    wait_for_tasks,
)
from zammad_pdf_archiver.app.jobs.ticket_storage import recover_archive_transactions
from zammad_pdf_archiver.app.jobs.ticket_stores import aclose_stores
from zammad_pdf_archiver.app.middleware.body_size_limit import BodySizeLimitMiddleware
from zammad_pdf_archiver.app.middleware.hmac_verify import HmacVerifyMiddleware
from zammad_pdf_archiver.app.middleware.rate_limit import RateLimitMiddleware
from zammad_pdf_archiver.app.middleware.request_id import (
    _REQUEST_ID_HEADER,
    RequestIdMiddleware,
)
from zammad_pdf_archiver.app.responses import api_error
from zammad_pdf_archiver.app.routes.healthz import router as healthz_router
from zammad_pdf_archiver.app.routes.ingest import router as ingest_router
from zammad_pdf_archiver.app.routes.jobs import router as jobs_router
from zammad_pdf_archiver.app.routes.metrics import router as metrics_router
from zammad_pdf_archiver.config.settings import Settings


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Track graceful shutdown for in-process jobs."""
    clear_shutting_down()
    settings = getattr(application.state, "settings", None)
    if settings is not None:
        await asyncio.to_thread(
            recover_archive_transactions,
            settings.storage.root,
            fsync=settings.storage.fsync,
        )
    try:
        yield
    finally:
        set_shutting_down()
        admission = getattr(application.state, "admission", None)
        if admission is not None:
            await admission.close()
        timeout = settings.admission.shutdown_timeout_seconds if settings is not None else 1.0
        await wait_for_tasks(timeout=timeout)
        await aclose_stores()


async def _global_exception_handler(request: Request, _exc: Exception) -> Response:
    request_id = getattr(request.state, "request_id", None)
    response = api_error(
        500,
        "An internal server error occurred.",
        code="internal_error",
        request_id=request_id,
    )
    if request_id:
        response.headers[_REQUEST_ID_HEADER] = request_id
    return response


def _wire_app(application: FastAPI, *, settings: Settings | None) -> None:
    application.state.settings = settings
    application.state.admission = (
        JobAdmission(
            max_pending=settings.admission.max_pending,
            max_running=settings.admission.max_running,
        )
        if settings is not None
        else None
    )
    application.add_middleware(HmacVerifyMiddleware, settings=settings)
    application.add_middleware(BodySizeLimitMiddleware, settings=settings)
    application.add_middleware(RateLimitMiddleware, settings=settings)
    application.add_middleware(RequestIdMiddleware)
    application.add_exception_handler(Exception, _global_exception_handler)
    application.include_router(healthz_router)
    application.include_router(ingest_router)
    if settings is not None and settings.observability.history_enabled:
        application.include_router(jobs_router)
    if settings is not None and settings.observability.metrics_enabled:
        application.include_router(metrics_router)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the FastAPI application with middleware, routes, and lifespan."""
    application = FastAPI(title="zammad-pdf-archiver", version=__version__, lifespan=lifespan)
    _wire_app(application, settings=settings)
    return application


app = create_app()
