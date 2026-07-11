from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, Request
from starlette.responses import Response

from zammad_pdf_archiver._version import __version__
from zammad_pdf_archiver.app.admin.auth import AdminSessionStore
from zammad_pdf_archiver.app.admin.security import AdminSecurityHeadersMiddleware
from zammad_pdf_archiver.app.jobs.admission import JobAdmission
from zammad_pdf_archiver.app.jobs.shutdown import (
    clear_shutting_down,
    set_shutting_down,
    wait_for_tasks,
)
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
from zammad_pdf_archiver.config.managed import ManagedConfigStore
from zammad_pdf_archiver.config.settings import Settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Track graceful shutdown for in-process jobs."""
    clear_shutting_down()
    try:
        yield
    finally:
        set_shutting_down()
        admission = getattr(app.state, "admission", None)
        if admission is not None:
            await admission.close()
        settings = getattr(app.state, "settings", None)
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


def _wire_app(app: FastAPI, *, settings: Settings | None) -> None:
    app.state.settings = settings
    app.state.process_started_at = datetime.now(UTC)
    app.state.admission = (
        JobAdmission(
            max_pending=settings.admission.max_pending,
            max_running=settings.admission.max_running,
        )
        if settings is not None
        else None
    )
    app.add_middleware(HmacVerifyMiddleware, settings=settings)
    app.add_middleware(BodySizeLimitMiddleware, settings=settings)
    app.add_middleware(RateLimitMiddleware, settings=settings)
    app.add_middleware(RequestIdMiddleware)
    app.add_exception_handler(Exception, _global_exception_handler)
    app.include_router(healthz_router)
    app.include_router(ingest_router)
    if settings is not None and settings.observability.history_enabled:
        app.include_router(jobs_router)
    if settings is not None and settings.observability.metrics_enabled:
        app.include_router(metrics_router)
    if settings is not None and settings.admin.enabled:
        from zammad_pdf_archiver.app.admin.routes import router as admin_router

        store = ManagedConfigStore(settings.admin.state_dir)
        app.state.managed_config_store = store
        app.state.active_config_revision = store.current_revision()
        app.state.admin_sessions = AdminSessionStore(
            idle_seconds=settings.admin.session_idle_seconds,
            absolute_seconds=settings.admin.session_absolute_seconds,
        )
        app.add_middleware(AdminSecurityHeadersMiddleware)
        app.include_router(admin_router)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the FastAPI application with middleware, routes, and lifespan."""
    app = FastAPI(title="zammad-pdf-archiver", version=__version__, lifespan=lifespan)
    _wire_app(app, settings=settings)
    return app


app = create_app()
