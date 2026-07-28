"""Managed-configuration revision page and JSON routes."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict
from starlette.responses import JSONResponse, RedirectResponse, Response

from chronikwerk.app.admin._config_routes import (
    _invalid_config_error,
    _normalized_overlay,
    _security_acknowledgement_error,
)
from chronikwerk.app.admin._route_support import (
    _api_error,
    _api_session,
    _html_session,
    _render,
    _request_id,
    _settings,
    _urlencoded,
)
from chronikwerk.app.admin.auth import csrf_token_matches, session_from_request
from chronikwerk.config.managed import (
    ManagedConfigError,
    RevisionConflict,
    flatten_mapping,
    validate_candidate,
)


class RestoreRequest(BaseModel):
    """Accept a revision identifier to restore as the managed configuration."""

    model_config = ConfigDict(extra="forbid")
    security_acknowledged: bool = False


async def revisions_page(
    request: Request,
    restore_error: bool = False,
    acknowledgement_required: bool = False,
) -> Response:
    """Render retained managed-configuration revisions for administrators."""
    session, redirect = _html_session(request)
    if redirect is not None or session is None:
        return redirect or RedirectResponse("/admin/login", status_code=303)
    store = request.app.state.managed_config_store
    return _render(
        "revisions.html",
        request=request,
        session=session,
        current="revisions",
        revisions=store.list_revisions(),
        current_revision=store.current_revision(),
        restore_error=restore_error,
        acknowledgement_required=acknowledgement_required,
    )


async def restore_form(request: Request, revision: str) -> Response:
    """Restore a retained configuration revision after CSRF validation."""
    data = await _urlencoded(request)
    session = session_from_request(request)
    if session is None or not csrf_token_matches(data.get("csrf_token"), session):
        return RedirectResponse("/admin/login", status_code=303)
    if data.get("security_acknowledged") != "true":
        return RedirectResponse(
            "/admin/configuration/revisions?acknowledgement_required=true",
            status_code=303,
        )
    try:
        overlay = request.app.state.managed_config_store.revision_overlay(revision)
        validate_candidate(_settings(request), overlay)
        request.app.state.managed_config_store.restore(
            revision,
            expected_revision=data.get("expected_revision", ""),
            request_id=_request_id(request),
        )
    except ManagedConfigError, OSError, RevisionConflict, ValueError:
        return RedirectResponse(
            "/admin/configuration/revisions?restore_error=true",
            status_code=303,
        )
    return RedirectResponse("/admin/configuration", status_code=303)


async def revisions_api(request: Request) -> Response:
    """Return retained configuration revisions without exposing secrets."""
    session, error = _api_session(request)
    if error is not None or session is None:
        return error or Response(status_code=401)
    store = request.app.state.managed_config_store
    return JSONResponse({"items": store.list_revisions(), "revision": store.current_revision()})


async def restore_api(
    request: Request,
    payload: RestoreRequest,
    revision: str,
) -> Response:
    """Restore a requested configuration revision through the JSON API."""
    session, error = _api_session(request, csrf=True)
    if error is not None or session is None:
        return error or Response(status_code=401)
    expected = request.headers.get("If-Match", "")
    try:
        overlay = request.app.state.managed_config_store.revision_overlay(revision)
        normalized = _normalized_overlay(_settings(request), overlay)
        acknowledgement_error = _security_acknowledgement_error(
            request,
            session,
            flatten_mapping(normalized),
            acknowledged=payload.security_acknowledged,
        )
        if acknowledgement_error is not None:
            return acknowledgement_error
        metadata = request.app.state.managed_config_store.restore(
            revision,
            expected_revision=expected,
            request_id=_request_id(request),
        )
    except RevisionConflict:
        return _api_error(
            request,
            409,
            "config_revision_conflict",
            "admin.restart_required",
            locale=session.locale,
        )
    except (ManagedConfigError, ValueError, OSError) as exc:
        return _invalid_config_error(
            request,
            session,
            exc,
            code="config_restore_failed",
        )
    return JSONResponse({**metadata, "restart_required": True})


def register_revision_page_routes(router: APIRouter) -> None:
    """Register this route group during application startup."""
    router.add_api_route(
        "/configuration/revisions",
        revisions_page,
        methods=["GET"],
    )
    router.add_api_route(
        "/configuration/revisions/{revision}/restore",
        restore_form,
        methods=["POST"],
    )


def register_revision_api_routes(router: APIRouter) -> None:
    """Register this route group during application startup."""
    router.add_api_route(
        "/api/v1/config/revisions",
        revisions_api,
        methods=["GET"],
    )
    router.add_api_route(
        "/api/v1/config/revisions/{revision}/restore",
        restore_api,
        methods=["POST"],
    )
