"""Aggregate multilingual HTML and JSON routes for the admin application."""

from __future__ import annotations

from fastapi import APIRouter, Request

from chronikwerk.app.admin._config_routes import (
    ConfigValidateRequest,
    StageRequest,
    _security_change_requires_ack,
    config_api,
    configuration_page,
    register_config_api_routes,
    register_config_page_routes,
    stage_config_api,
    validate_config_api,
)
from chronikwerk.app.admin._page_routes import (
    _STATUS_OPTIONS,
    admin_css,
    admin_javascript,
    brand_mark,
    change_locale,
    jobs_page,
    login_form,
    login_page,
    logout_form,
    overview_page,
    register_page_routes,
    retry_form,
    ticket_history_page,
)
from chronikwerk.app.admin._revision_routes import (
    RestoreRequest,
    register_revision_api_routes,
    register_revision_page_routes,
    restore_api,
    restore_form,
    revisions_api,
    revisions_page,
)
from chronikwerk.app.admin._route_support import (
    _api_error,
    _api_session,
    _configure_retry_scheduler,
    _decorate_history,
    _display_timestamp,
    _html_session,
    _locale,
    _next_history_cursor,
    _render,
    _request_id,
    _safe_admin_referer,
    _safe_next,
    _set_session_cookie,
    _settings,
    _urlencoded,
)
from chronikwerk.app.admin._status_routes import (
    RetryRequest,
    SessionRequest,
    create_session,
    delete_session,
    jobs_api,
    register_status_routes,
    retry_api,
    status_api,
    storage_check_api,
)
from chronikwerk.app.routes.healthz import _check_storage
from chronikwerk.app.routes.ingest import schedule_retry
from chronikwerk.config.settings import Settings

__all__ = [
    "ConfigValidateRequest",
    "RestoreRequest",
    "RetryRequest",
    "SessionRequest",
    "StageRequest",
    "_STATUS_OPTIONS",
    "_api_error",
    "_api_session",
    "_check_storage",
    "_decorate_history",
    "_display_timestamp",
    "_html_session",
    "_locale",
    "_next_history_cursor",
    "_render",
    "_request_id",
    "_safe_admin_referer",
    "_safe_next",
    "_security_change_requires_ack",
    "_set_session_cookie",
    "_settings",
    "_urlencoded",
    "admin_css",
    "admin_javascript",
    "brand_mark",
    "change_locale",
    "config_api",
    "configuration_page",
    "create_session",
    "delete_session",
    "jobs_api",
    "jobs_page",
    "login_form",
    "login_page",
    "logout_form",
    "overview_page",
    "restore_api",
    "restore_form",
    "retry_api",
    "retry_form",
    "revisions_api",
    "revisions_page",
    "router",
    "schedule_retry",
    "stage_config_api",
    "status_api",
    "storage_check_api",
    "ticket_history_page",
    "validate_config_api",
]

router = APIRouter(prefix="/admin", include_in_schema=False)


def _schedule_retry_via_export(
    request: Request,
    *,
    ticket_id: int,
    settings: Settings,
) -> bool:
    """Preserve the aggregate module's retry override seam without a back import."""
    return schedule_retry(request, ticket_id=ticket_id, settings=settings)


_configure_retry_scheduler(_schedule_retry_via_export)

# Preserve the original route order while each private module owns a coherent group.
register_page_routes(router)
register_config_page_routes(router)
register_revision_page_routes(router)
register_status_routes(router)
register_config_api_routes(router)
register_revision_api_routes(router)
