from __future__ import annotations

import base64
import binascii
import functools
import pathlib

import structlog
from fastapi import APIRouter, HTTPException, Path, Query, Request
from starlette.responses import HTMLResponse

from zammad_pdf_archiver.app.constants import FORCE_REPROCESS_KEY, REQUEST_ID_KEY
from zammad_pdf_archiver.app.jobs.redis_queue import get_queue_stats, replay_dlq
from zammad_pdf_archiver.app.responses import (
    constant_time_token_match,
    settings_or_503,
    verify_bearer_auth,
)
from zammad_pdf_archiver.app.routes import ingest as ingest_routes
from zammad_pdf_archiver.app.routes import operations
from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.config.validate import (
    ConfigValidationError,
    validate_settings,
)

router = APIRouter()
log = structlog.get_logger(__name__)

_DASHBOARD_PATH = (
    pathlib.Path(__file__).resolve().parent.parent.parent / "templates" / "admin" / "dashboard.html"
)
_ADMIN_BASIC_CHALLENGE = 'Basic realm="zammad-pdf-archiver-admin"'


@functools.cache
def _read_dashboard_html() -> str:
    return _DASHBOARD_PATH.read_text(encoding="utf-8")


def _verify_admin_auth(request: Request, settings: Settings) -> None:
    if not settings.admin.enabled:
        raise HTTPException(status_code=404, detail="admin_disabled")
    verify_bearer_auth(request, settings)


def _dashboard_auth_error() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="unauthorized",
        headers={"WWW-Authenticate": _ADMIN_BASIC_CHALLENGE},
    )


def _admin_token_bytes(settings: Settings) -> bytes:
    token = settings.admin.bearer_token
    expected = token.get_secret_value().encode("utf-8") if token is not None else b""
    if not expected:
        raise HTTPException(status_code=503, detail="admin_token_not_configured")
    return expected


def _verify_admin_dashboard_auth(request: Request, settings: Settings) -> None:
    if not settings.admin.enabled:
        raise HTTPException(status_code=404, detail="admin_disabled")

    expected = _admin_token_bytes(settings)
    auth = request.headers.get("Authorization", "")
    if _dashboard_bearer_auth_ok(auth, expected):
        return
    if _dashboard_basic_auth_ok(auth, expected):
        return
    raise _dashboard_auth_error()


def _dashboard_bearer_auth_ok(auth: str, expected: bytes) -> bool:
    if not auth.startswith("Bearer ") or len(auth) < 8:
        return False
    if constant_time_token_match(expected, auth[7:].strip().encode("utf-8")):
        return True
    raise _dashboard_auth_error()


def _dashboard_basic_auth_ok(auth: str, expected: bytes) -> bool:
    if not auth.startswith("Basic ") or len(auth) < 7:
        return False
    try:
        decoded = base64.b64decode(auth[6:].strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        raise _dashboard_auth_error() from None

    _, separator, password = decoded.partition(":")
    return bool(separator and constant_time_token_match(expected, password.encode("utf-8")))


@router.get("/admin", response_class=HTMLResponse)
def admin_dashboard(request: Request) -> HTMLResponse:
    settings = settings_or_503(request)
    _verify_admin_dashboard_auth(request, settings)
    return HTMLResponse(content=_read_dashboard_html())


@router.get("/admin/api/queue/stats")
async def admin_queue_stats(request: Request) -> dict[str, object]:
    settings = settings_or_503(request)
    _verify_admin_auth(request, settings)
    try:
        return await get_queue_stats(settings)
    except Exception as exc:
        log.warning("admin.queue_stats_unavailable")
        raise HTTPException(status_code=503, detail="queue_unavailable") from exc


@router.get("/admin/api/history")
async def admin_history(
    request: Request,
    limit: int | None = None,
    # Security: reject non-positive ticket IDs at the parameter level.
    ticket_id: int | None = Query(default=None, ge=1),
) -> dict[str, object]:
    settings = settings_or_503(request)
    _verify_admin_auth(request, settings)

    resolved_limit = limit if limit is not None else settings.admin.history_limit
    bounded_limit = max(1, min(int(resolved_limit), 5000))
    try:
        return await operations.history_payload(
            settings,
            limit=bounded_limit,
            ticket_id=ticket_id,
        )
    except Exception as exc:
        log.warning("admin.history_unavailable")
        raise HTTPException(status_code=503, detail="history_unavailable") from exc


@router.post("/admin/api/retry/{ticket_id}")
async def admin_retry_ticket(
    request: Request,
    # Security: reject non-positive ticket IDs at the parameter level.
    ticket_id: int = Path(..., ge=1),
) -> dict[str, object]:
    settings = settings_or_503(request)
    _verify_admin_auth(request, settings)

    payload: dict[str, object] = {
        "ticket_id": ticket_id,
        REQUEST_ID_KEY: getattr(request.state, "request_id", None),
        FORCE_REPROCESS_KEY: True,
    }
    try:
        await ingest_routes.dispatch_ticket(
            delivery_id=None,
            payload_for_job=payload,
            settings=settings,
        )
    except Exception as exc:
        log.warning("admin.retry_dispatch_unavailable", ticket_id=ticket_id)
        raise HTTPException(status_code=503, detail="queue_unavailable") from exc
    return {"status": "accepted", "ticket_id": ticket_id}


@router.post("/admin/api/dlq/drain")
async def admin_drain_dlq(request: Request, limit: int = 100) -> dict[str, object]:
    settings = settings_or_503(request)
    _verify_admin_auth(request, settings)

    bounded_limit = max(1, min(int(limit), 1000))
    try:
        return await operations.drain_dlq_payload(settings, limit=bounded_limit)
    except Exception as exc:
        log.warning("admin.dlq_unavailable")
        raise HTTPException(status_code=503, detail="dlq_unavailable") from exc


@router.post("/admin/api/dlq/replay")
async def admin_replay_dlq(
    request: Request,
    limit: int = 10,
) -> dict[str, object]:
    settings = settings_or_503(request)
    _verify_admin_auth(request, settings)

    bounded_limit = max(1, min(int(limit), 1000))
    try:
        replay_result = await replay_dlq(settings, limit=bounded_limit)
        incomplete = (
            replay_result["skipped"] or replay_result["errors"] or replay_result["not_deleted"]
        )
        return {
            "status": "partial" if incomplete else "ok",
            "idempotent": False,
            "duplicate_risk": replay_result["not_deleted"],
            **replay_result,
        }
    except Exception as exc:
        log.warning("admin.dlq_replay_unavailable")
        raise HTTPException(
            status_code=503,
            detail="dlq_unavailable",
        ) from exc


@router.get("/admin/api/config/check")
async def admin_config_check(request: Request) -> dict[str, object]:
    settings = settings_or_503(request)
    _verify_admin_auth(request, settings)

    issues: list[dict[str, str]] = []
    try:
        validate_settings(settings)
    except ConfigValidationError as exc:
        issues = [{"path": i.path, "message": i.message} for i in exc.issues]

    checks: dict[str, object] = {
        "storage_root_exists": settings.storage.root.is_dir(),
        "signing_enabled": settings.signing.enabled,
    }
    if settings.signing.enabled and settings.signing.pfx_path:
        checks["pfx_file_exists"] = settings.signing.pfx_path.is_file()

    return {
        "valid": len(issues) == 0,
        "issues": issues,
        "checks": checks,
    }
