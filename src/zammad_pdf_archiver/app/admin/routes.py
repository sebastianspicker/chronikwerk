"""Multilingual HTML and JSON routes for the feature-flagged admin application."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from importlib import resources
from typing import Annotated, Any
from urllib.parse import parse_qs

from fastapi import APIRouter, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from zammad_pdf_archiver._version import __version__
from zammad_pdf_archiver.app.admin.auth import (
    SESSION_COOKIE,
    AdminSession,
    access_token_matches,
    csrf_matches,
    csrf_token_matches,
    session_from_request,
)
from zammad_pdf_archiver.app.admin.templates import render_admin_template
from zammad_pdf_archiver.app.jobs.history import read_history
from zammad_pdf_archiver.app.routes.healthz import _check_storage
from zammad_pdf_archiver.app.routes.ingest import schedule_retry
from zammad_pdf_archiver.config.managed import (
    ManagedConfigError,
    RevisionConflict,
    config_read_model,
    environment_owns,
    flatten_mapping,
    get_path,
    overlay_from_flat,
    secret_presence,
    validate_candidate,
    validation_errors,
)
from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.i18n import normalize_locale, translate

router = APIRouter(prefix="/admin", include_in_schema=False)

_STATUS_OPTIONS = ("accepted", "running", "processed", "failed", "skipped")


class SessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    access_token: str = Field(min_length=1, max_length=4096)
    locale: str | None = None


class RetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    acknowledge_overwrite: bool


class ConfigValidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    values: dict[str, Any]
    security_acknowledged: bool = False


class StageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    overlay: dict[str, Any]
    security_acknowledged: bool = False


class RestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    security_acknowledged: bool = False


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", "") or "")


def _locale(request: Request, session: AdminSession | None = None) -> str:
    if session is not None:
        return session.locale
    return normalize_locale(
        request.query_params.get("lang"),
        default=_settings(request).admin.default_locale,
    )


def _api_error(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    *,
    locale: str | None = None,
    **extra: Any,
) -> JSONResponse:
    content: dict[str, Any] = {
        "code": code,
        "message": translate(locale or _locale(request), message),
        "request_id": _request_id(request),
        **extra,
    }
    return JSONResponse(status_code=status_code, content=content)


def _api_session(
    request: Request, *, csrf: bool = False
) -> tuple[AdminSession | None, Response | None]:
    session = session_from_request(request)
    if session is None:
        return None, _api_error(
            request,
            401,
            "admin_session_required",
            "admin.session_expired",
        )
    if csrf and not csrf_matches(request, session):
        return None, _api_error(
            request,
            403,
            "csrf_invalid",
            "admin.session_expired",
            locale=session.locale,
        )
    return session, None


def _html_session(request: Request) -> tuple[AdminSession | None, RedirectResponse | None]:
    session = session_from_request(request)
    if session is None:
        next_path = request.url.path
        if request.url.query:
            next_path = f"{next_path}?{request.url.query}"
        return None, RedirectResponse(f"/admin/login?next={next_path}", status_code=303)
    return session, None


def _safe_next(value: str | None) -> str:
    if value and value.startswith("/admin") and not value.startswith("//"):
        return value
    return "/admin"


async def _urlencoded(request: Request) -> dict[str, str]:
    data = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
    return {key: values[-1] for key, values in data.items() if values}


def _set_session_cookie(response: Response, request: Request, session: AdminSession) -> None:
    settings = _settings(request).admin
    response.set_cookie(
        SESSION_COOKIE,
        session.session_id,
        max_age=settings.session_absolute_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/admin",
    )


def _display_timestamp(value: float, locale: str) -> str:
    timestamp = datetime.fromtimestamp(value, tz=UTC)
    fmt = "%d.%m.%Y %H:%M:%S UTC" if locale == "de-DE" else "%d/%m/%Y %H:%M:%S UTC"
    return timestamp.strftime(fmt)


def _decorate_history(items: list[dict[str, Any]], locale: str) -> list[dict[str, Any]]:
    return [
        {**item, "created_display": _display_timestamp(float(item["created_at"]), locale)}
        for item in items
    ]


def _render(
    template: str, *, request: Request, session: AdminSession | None, **context: Any
) -> HTMLResponse:
    locale = _locale(request, session)
    html = render_admin_template(
        template,
        locale=locale,
        request=request,
        session=session,
        **context,
    )
    return HTMLResponse(html)


@router.get("/static/admin.css")
async def admin_css() -> Response:
    data = resources.files("zammad_pdf_archiver").joinpath("static/admin/admin.css").read_bytes()
    return Response(data, media_type="text/css; charset=utf-8")


@router.get("/static/admin.js")
async def admin_javascript() -> Response:
    data = resources.files("zammad_pdf_archiver").joinpath("static/admin/admin.js").read_bytes()
    return Response(data, media_type="text/javascript; charset=utf-8")


@router.get("/login")
async def login_page(request: Request, next: str | None = None, error: bool = False) -> Response:
    if session_from_request(request) is not None:
        return RedirectResponse(_safe_next(next), status_code=303)
    return _render(
        "login.html",
        request=request,
        session=None,
        next_path=_safe_next(next),
        error=error,
        current="login",
    )


@router.post("/login")
async def login_form(request: Request) -> Response:
    data = await _urlencoded(request)
    settings = _settings(request)
    locale = normalize_locale(data.get("locale"), default=settings.admin.default_locale)
    if not access_token_matches(data.get("access_token", ""), settings.admin.access_token):
        return RedirectResponse(
            f"/admin/login?error=true&lang={locale}&next={_safe_next(data.get('next'))}",
            status_code=303,
        )
    request.app.state.admin_sessions.delete(request.cookies.get(SESSION_COOKIE))
    session = request.app.state.admin_sessions.create(locale=locale)
    response = RedirectResponse(_safe_next(data.get("next")), status_code=303)
    _set_session_cookie(response, request, session)
    return response


@router.post("/logout")
async def logout_form(request: Request) -> Response:
    data = await _urlencoded(request)
    session = session_from_request(request)
    if session is None or not csrf_token_matches(data.get("csrf_token"), session):
        return RedirectResponse("/admin/login", status_code=303)
    request.app.state.admin_sessions.delete(session.session_id)
    response = RedirectResponse("/admin/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/admin")
    return response


@router.post("/locale")
async def change_locale(request: Request) -> Response:
    data = await _urlencoded(request)
    session = session_from_request(request)
    if session is None or not csrf_token_matches(data.get("csrf_token"), session):
        return RedirectResponse("/admin/login", status_code=303)
    session.locale = normalize_locale(data.get("locale"), default=session.locale)
    target = request.headers.get("referer", "/admin")
    if not target.startswith(str(request.base_url).rstrip("/")):
        target = "/admin"
    return RedirectResponse(target, status_code=303)


@router.get("")
async def overview_page(request: Request) -> Response:
    session, redirect = _html_session(request)
    if redirect is not None or session is None:
        return redirect or RedirectResponse("/admin/login", status_code=303)
    locale = session.locale
    now = datetime.now(UTC)
    started: datetime = request.app.state.process_started_at
    store = request.app.state.managed_config_store
    current_revision = store.current_revision()
    active_revision = request.app.state.active_config_revision
    failures = _decorate_history(read_history(10, statuses={"failed"}), locale)
    return _render(
        "overview.html",
        request=request,
        session=session,
        current="overview",
        now_iso=now.isoformat(),
        now_display=_display_timestamp(now.timestamp(), locale),
        process_started_iso=started.isoformat(),
        process_started_display=_display_timestamp(started.timestamp(), locale),
        version=__version__,
        admission=request.app.state.admission,
        active_revision=active_revision,
        staged_revision=current_revision if current_revision != active_revision else None,
        failures=failures,
    )


@router.get("/jobs")
async def jobs_page(
    request: Request,
    ticket_id: int | None = Query(default=None, ge=1),
    status: str | None = None,
    before_id: int | None = Query(default=None, ge=1),
) -> Response:
    session, redirect = _html_session(request)
    if redirect is not None or session is None:
        return redirect or RedirectResponse("/admin/login", status_code=303)
    statuses = {status} if status else None
    items = read_history(51, ticket_id, before_id=before_id, statuses=statuses)
    next_cursor = int(items[-1]["id"]) if len(items) > 50 else None
    return _render(
        "jobs.html",
        request=request,
        session=session,
        current="jobs",
        items=_decorate_history(items[:50], session.locale),
        next_cursor=next_cursor,
        ticket_id=ticket_id,
        status=status,
        status_options=_STATUS_OPTIONS,
    )


@router.get("/jobs/{ticket_id}")
async def ticket_history_page(
    request: Request,
    ticket_id: int = Path(..., ge=1),
    accepted: bool = False,
    request_id: str | None = None,
) -> Response:
    session, redirect = _html_session(request)
    if redirect is not None or session is None:
        return redirect or RedirectResponse("/admin/login", status_code=303)
    return _render(
        "job_detail.html",
        request=request,
        session=session,
        current="jobs",
        ticket_id=ticket_id,
        items=_decorate_history(read_history(100, ticket_id), session.locale),
        accepted=accepted,
        request_id=request_id,
    )


@router.post("/jobs/{ticket_id}/retry")
async def retry_form(request: Request, ticket_id: int = Path(..., ge=1)) -> Response:
    data = await _urlencoded(request)
    session = session_from_request(request)
    if (
        session is None
        or not csrf_token_matches(data.get("csrf_token"), session)
        or data.get("acknowledge_overwrite") != "true"
    ):
        return RedirectResponse(f"/admin/jobs/{ticket_id}", status_code=303)
    if not schedule_retry(request, ticket_id=ticket_id, settings=_settings(request)):
        return Response(status_code=503, headers={"Retry-After": "1"})
    return RedirectResponse(
        f"/admin/jobs/{ticket_id}?accepted=true&request_id={_request_id(request)}",
        status_code=303,
    )


@router.get("/configuration")
async def configuration_page(request: Request) -> Response:
    session, redirect = _html_session(request)
    if redirect is not None or session is None:
        return redirect or RedirectResponse("/admin/login", status_code=303)
    store = request.app.state.managed_config_store
    current_revision = store.current_revision()
    groups: dict[str, list[dict[str, Any]]] = {}
    for field in config_read_model(_settings(request), store.load()):
        groups.setdefault(str(field["group"]), []).append(field)
    return _render(
        "configuration.html",
        request=request,
        session=session,
        current="configuration",
        field_groups=groups,
        current_revision=current_revision,
        staged_revision=(
            current_revision
            if current_revision != request.app.state.active_config_revision
            else None
        ),
    )


@router.get("/configuration/revisions")
async def revisions_page(request: Request) -> Response:
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
    )


@router.post("/configuration/revisions/{revision}/restore")
async def restore_form(request: Request, revision: str) -> Response:
    data = await _urlencoded(request)
    session = session_from_request(request)
    if session is None or not csrf_token_matches(data.get("csrf_token"), session):
        return RedirectResponse("/admin/login", status_code=303)
    if data.get("security_acknowledged") != "true":
        return RedirectResponse("/admin/configuration/revisions", status_code=303)
    try:
        overlay = request.app.state.managed_config_store.revision_overlay(revision)
        validate_candidate(_settings(request), overlay)
        request.app.state.managed_config_store.restore(
            revision,
            expected_revision=data.get("expected_revision", ""),
            request_id=_request_id(request),
        )
    except (ManagedConfigError, OSError, ValueError):
        return RedirectResponse("/admin/configuration/revisions", status_code=303)
    return RedirectResponse("/admin/configuration", status_code=303)


@router.post("/api/v1/session")
async def create_session(request: Request, payload: SessionRequest) -> Response:
    settings = _settings(request)
    if not access_token_matches(payload.access_token, settings.admin.access_token):
        return _api_error(
            request,
            401,
            "invalid_credentials",
            "admin.invalid_credentials",
            locale=normalize_locale(payload.locale, default=settings.admin.default_locale),
        )
    request.app.state.admin_sessions.delete(request.cookies.get(SESSION_COOKIE))
    session = request.app.state.admin_sessions.create(
        locale=normalize_locale(payload.locale, default=settings.admin.default_locale)
    )
    response = Response(status_code=204)
    _set_session_cookie(response, request, session)
    return response


@router.delete("/api/v1/session")
async def delete_session(request: Request) -> Response:
    session, error = _api_session(request, csrf=True)
    if error is not None or session is None:
        return error or Response(status_code=401)
    request.app.state.admin_sessions.delete(session.session_id)
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, path="/admin")
    return response


@router.get("/api/v1/status")
async def status_api(request: Request) -> Response:
    session, error = _api_session(request)
    if error is not None or session is None:
        return error or Response(status_code=401)
    admission = request.app.state.admission
    store = request.app.state.managed_config_store
    current = store.current_revision()
    return JSONResponse(
        {
            "service": "zammad-pdf-archiver",
            "version": __version__,
            "process_started_at": request.app.state.process_started_at.isoformat(),
            "health": {"status": "ok"},
            "admission": {
                "pending": admission.pending,
                "running": admission.running,
                "max_pending": admission.max_pending,
                "max_running": admission.max_running,
                "closing": admission.closing,
            },
            "history": {"volatile": True, "limit": 5000},
            "config": {
                "active_revision": request.app.state.active_config_revision,
                "staged_revision": (
                    current if current != request.app.state.active_config_revision else None
                ),
            },
        }
    )


@router.post("/api/v1/status/storage-check")
async def storage_check_api(request: Request) -> Response:
    session, error = _api_session(request, csrf=True)
    if error is not None or session is None:
        return error or Response(status_code=401)
    result = await asyncio.to_thread(_check_storage, _settings(request))
    return JSONResponse({"storage": result, "request_id": _request_id(request)})


@router.get("/api/v1/jobs")
async def jobs_api(
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    before_id: int | None = Query(default=None, ge=1),
    ticket_id: int | None = Query(default=None, ge=1),
    status: Annotated[list[str] | None, Query()] = None,
) -> Response:
    session, error = _api_session(request)
    if error is not None or session is None:
        return error or Response(status_code=401)
    items = read_history(
        limit + 1,
        ticket_id,
        before_id=before_id,
        statuses=set(status or []),
    )
    next_cursor = int(items[-1]["id"]) if len(items) > limit else None
    return JSONResponse(
        {
            "items": items[:limit],
            "next_cursor": next_cursor,
            "process_started_at": request.app.state.process_started_at.isoformat(),
            "volatile": True,
        }
    )


@router.post("/api/v1/jobs/{ticket_id}/retry")
async def retry_api(
    request: Request,
    payload: RetryRequest,
    ticket_id: int = Path(..., ge=1),
) -> Response:
    session, error = _api_session(request, csrf=True)
    if error is not None or session is None:
        return error or Response(status_code=401)
    if not payload.acknowledge_overwrite:
        return _api_error(
            request,
            422,
            "overwrite_acknowledgement_required",
            "admin.retry_warning",
            locale=session.locale,
        )
    if not schedule_retry(request, ticket_id=ticket_id, settings=_settings(request)):
        response = _api_error(
            request,
            503,
            "job_capacity_exhausted",
            "admin.retry_warning",
            locale=session.locale,
        )
        response.headers["Retry-After"] = "1"
        return response
    return JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "ticket_id": ticket_id,
            "request_id": _request_id(request),
        },
    )


@router.get("/api/v1/config")
async def config_api(request: Request) -> Response:
    session, error = _api_session(request)
    if error is not None or session is None:
        return error or Response(status_code=401)
    store = request.app.state.managed_config_store
    current = store.current_revision()
    return JSONResponse(
        {
            "fields": config_read_model(_settings(request), store.load()),
            "secret_presence": secret_presence(_settings(request)),
            "active_revision": request.app.state.active_config_revision,
            "staged_revision": (
                current if current != request.app.state.active_config_revision else None
            ),
            "revision": current,
            "restart_required": current != request.app.state.active_config_revision,
        }
    )


def _security_change_requires_ack(settings: Settings, values: dict[str, Any]) -> bool:
    current = settings.model_dump(mode="json")
    security_paths = {
        "hardening.transport.trust_env",
        "hardening.transport.allow_insecure_http",
        "hardening.transport.allow_private_networks",
    }
    return any(
        path in values and values[path] != get_path(current, path) for path in security_paths
    )


@router.post("/api/v1/config/validate")
async def validate_config_api(request: Request, payload: ConfigValidateRequest) -> Response:
    session, error = _api_session(request, csrf=True)
    if error is not None or session is None:
        return error or Response(status_code=401)
    locked = sorted(path for path in payload.values if environment_owns(path))
    if locked:
        return _api_error(
            request,
            422,
            "environment_owned_field",
            "admin.config_intro",
            locale=session.locale,
            errors=[
                {"path": path, "message": "Environment-owned field is read-only"} for path in locked
            ],
        )
    if (
        _security_change_requires_ack(_settings(request), payload.values)
        and not payload.security_acknowledged
    ):
        return _api_error(
            request,
            422,
            "security_acknowledgement_required",
            "admin.security_ack",
            locale=session.locale,
        )
    try:
        overlay = overlay_from_flat(payload.values)
        _candidate, normalized = validate_candidate(_settings(request), overlay)
    except (ManagedConfigError, ValueError) as exc:
        return _api_error(
            request,
            422,
            "config_invalid",
            "admin.config_intro",
            locale=session.locale,
            errors=validation_errors(exc),
        )
    current = _settings(request).model_dump(mode="json")
    diff = [
        {"path": path, "before": get_path(current, path), "after": value}
        for path, value in flatten_mapping(normalized).items()
        if get_path(current, path) != value
    ]
    return JSONResponse(
        {
            "valid": True,
            "overlay": normalized,
            "diff": diff,
            "revision": request.app.state.managed_config_store.current_revision(),
        }
    )


@router.put("/api/v1/config/staged")
async def stage_config_api(request: Request, payload: StageRequest) -> Response:
    session, error = _api_session(request, csrf=True)
    if error is not None or session is None:
        return error or Response(status_code=401)
    expected = request.headers.get("If-Match", "")
    try:
        locked = sorted(path for path in flatten_mapping(payload.overlay) if environment_owns(path))
        if locked:
            return _api_error(
                request,
                422,
                "environment_owned_field",
                "admin.config_intro",
                locale=session.locale,
                errors=[
                    {"path": path, "message": "Environment-owned field is read-only"}
                    for path in locked
                ],
            )
        _candidate, normalized = validate_candidate(_settings(request), payload.overlay)
        if (
            _security_change_requires_ack(_settings(request), flatten_mapping(normalized))
            and not payload.security_acknowledged
        ):
            return _api_error(
                request,
                422,
                "security_acknowledgement_required",
                "admin.security_ack",
                locale=session.locale,
            )
        metadata = request.app.state.managed_config_store.stage(
            normalized,
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
        return _api_error(
            request,
            422,
            "config_invalid",
            "admin.config_intro",
            locale=session.locale,
            errors=validation_errors(exc),
        )
    return JSONResponse(
        {
            **metadata,
            "restart_required": True,
        }
    )


@router.get("/api/v1/config/revisions")
async def revisions_api(request: Request) -> Response:
    session, error = _api_session(request)
    if error is not None or session is None:
        return error or Response(status_code=401)
    store = request.app.state.managed_config_store
    return JSONResponse({"items": store.list_revisions(), "revision": store.current_revision()})


@router.post("/api/v1/config/revisions/{revision}/restore")
async def restore_api(
    request: Request,
    payload: RestoreRequest,
    revision: str,
) -> Response:
    session, error = _api_session(request, csrf=True)
    if error is not None or session is None:
        return error or Response(status_code=401)
    expected = request.headers.get("If-Match", "")
    try:
        overlay = request.app.state.managed_config_store.revision_overlay(revision)
        _candidate, normalized = validate_candidate(_settings(request), overlay)
        if (
            _security_change_requires_ack(_settings(request), flatten_mapping(normalized))
            and not payload.security_acknowledged
        ):
            return _api_error(
                request,
                422,
                "security_acknowledgement_required",
                "admin.security_ack",
                locale=session.locale,
            )
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
        return _api_error(
            request,
            422,
            "config_restore_failed",
            "admin.config_intro",
            locale=session.locale,
            errors=validation_errors(exc),
        )
    return JSONResponse({**metadata, "restart_required": True})
