from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from importlib import metadata

import structlog
from fastapi import APIRouter, Request

from zammad_pdf_archiver.config.settings import Settings

router = APIRouter()
log = structlog.get_logger(__name__)
UTC = timezone.utc


def _service_version() -> str:
    try:
        return metadata.version("zammad-pdf-archiver")
    except metadata.PackageNotFoundError:
        return "0.0.0"


async def _check_redis(settings: Settings) -> dict[str, object]:
    redis_url = settings.workflow.redis_url
    if not redis_url or not redis_url.strip():
        return {"available": False, "reason": "not_configured"}
    try:
        from zammad_pdf_archiver.adapters.redis_pool import get_redis

        redis = await get_redis(redis_url)
        await redis.ping()
        return {"available": True}
    except Exception as exc:  # noqa: BLE001 -- health probe must not crash; redis errors are not stdlib
        return {"available": False, "reason": str(exc)[:200]}


def _check_storage(settings: Settings) -> dict[str, object]:
    root = settings.storage.root
    try:
        with tempfile.NamedTemporaryFile(dir=root, delete=True):
            return {"writable": True}
    except OSError as exc:
        return {"writable": False, "reason": str(exc)[:200]}


def _deep_check_healthy(name: str, result: object) -> bool | None:
    if not isinstance(result, dict):
        return None
    if name == "redis" and result.get("reason") == "not_configured":
        return None
    if "available" in result:
        return bool(result["available"])
    if "writable" in result:
        return bool(result["writable"])
    return None


@router.get("/healthz")
async def healthz(request: Request, deep: bool = False) -> dict[str, object]:
    """Return service health; include Redis and storage checks when deep=True."""
    out: dict[str, object] = {
        "status": "ok",
        "time": datetime.now(UTC).isoformat(),
    }
    settings = getattr(request.app.state, "settings", None)
    if settings is None or not settings.observability.healthz_omit_version:
        out["service"] = "zammad-pdf-archiver"
        out["version"] = _service_version()

    if deep and settings is not None:
        checks: dict[str, object] = {}
        checks["redis"] = await _check_redis(settings)
        checks["storage"] = _check_storage(settings)
        out["checks"] = checks
        healthy_checks = [
            result
            for name, value in checks.items()
            if (result := _deep_check_healthy(name, value)) is not None
        ]
        all_ok = bool(healthy_checks) and all(healthy_checks)
        if not all_ok:
            out["status"] = "degraded"

    return out
