from __future__ import annotations

import hmac
import pathlib

import structlog
from fastapi import APIRouter, HTTPException, Path, Query, Request
from starlette.responses import HTMLResponse

from zammad_pdf_archiver.app.constants import REQUEST_ID_KEY
from zammad_pdf_archiver.app.jobs.history import read_history
from zammad_pdf_archiver.app.jobs.redis_queue import (
    drain_dlq,
    get_queue_stats,
    replay_dlq,
)
from zammad_pdf_archiver.app.responses import settings_or_503
from zammad_pdf_archiver.app.routes.ingest import _dispatch_ticket
from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.config.validate import (
    ConfigValidationError,
    validate_settings,
)

router = APIRouter()
log = structlog.get_logger(__name__)

_DASHBOARD_HTML = (
    pathlib.Path(__file__).resolve().parent.parent.parent / "templates" / "admin" / "dashboard.html"
).read_text(encoding="utf-8")


def _verify_admin_auth(request: Request, settings: Settings) -> None:
    if not settings.admin.enabled:
        raise HTTPException(status_code=404, detail="admin_disabled")

    token = settings.admin.bearer_token
    expected = token.get_secret_value().encode("utf-8") if token is not None else b""
    if not expected:
        raise HTTPException(status_code=503, detail="admin_token_not_configured")

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or len(auth) < 8:
        raise HTTPException(status_code=401, detail="unauthorized")

    provided = auth[7:].strip().encode("utf-8")
    if not hmac.compare_digest(expected, provided):
        raise HTTPException(status_code=401, detail="unauthorized")


@router.get("/admin", response_class=HTMLResponse)
def admin_dashboard() -> HTMLResponse:
    return HTMLResponse(content=_DASHBOARD_HTML)


@router.get("/admin/api/queue/stats")
async def admin_queue_stats(request: Request) -> dict[str, object]:
    settings = settings_or_503(request)
    _verify_admin_auth(request, settings)
    try:
        stats = await get_queue_stats(settings)
    except Exception as exc:
        log.warning("admin.queue_stats_unavailable")
        raise HTTPException(status_code=503, detail="queue_unavailable") from exc
    return {str(k): v for k, v in stats.items()}


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
        items = await read_history(settings, limit=bounded_limit, ticket_id=ticket_id)
    except Exception as exc:
        log.warning("admin.history_unavailable")
        raise HTTPException(status_code=503, detail="history_unavailable") from exc
    return {"status": "ok", "count": len(items), "items": items}


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
    }
    try:
        await _dispatch_ticket(
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
        drained = await drain_dlq(settings, limit=bounded_limit)
    except Exception as exc:
        log.warning("admin.dlq_unavailable")
        raise HTTPException(status_code=503, detail="dlq_unavailable") from exc
    return {"status": "ok", "drained": drained}


@router.post("/admin/api/dlq/replay")
async def admin_replay_dlq(
    request: Request, limit: int = 10,
) -> dict[str, object]:
    settings = settings_or_503(request)
    _verify_admin_auth(request, settings)

    bounded_limit = max(1, min(int(limit), 1000))
    try:
        replayed = await replay_dlq(settings, limit=bounded_limit)
    except Exception as exc:
        log.warning("admin.dlq_replay_unavailable")
        raise HTTPException(
            status_code=503, detail="dlq_unavailable",
        ) from exc
    return {"status": "ok", "replayed": replayed}


@router.get("/admin/api/config/check")
async def admin_config_check(request: Request) -> dict[str, object]:
    settings = settings_or_503(request)
    _verify_admin_auth(request, settings)

    issues: list[dict[str, str]] = []
    try:
        validate_settings(settings)
    except ConfigValidationError as exc:
        issues = [
            {"path": i.path, "message": i.message} for i in exc.issues
        ]

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
