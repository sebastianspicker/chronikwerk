from __future__ import annotations

from datetime import UTC, datetime
from importlib import metadata
from typing import Any

import structlog
from fastapi import APIRouter, Request

router = APIRouter()
log = structlog.get_logger(__name__)


def _service_version() -> str:
    try:
        return metadata.version("zammad-pdf-archiver")
    except metadata.PackageNotFoundError:
        return "0.0.0"


async def _check_redis(settings: Any) -> dict[str, object]:
    redis_url = getattr(settings.workflow, "redis_url", None)
    if not redis_url or not redis_url.strip():
        return {"available": False, "reason": "not_configured"}
    try:
        from zammad_pdf_archiver.adapters.redis_pool import get_redis

        redis = await get_redis(redis_url)
        await redis.ping()
        return {"available": True}
    except Exception as exc:
        return {"available": False, "reason": str(exc)[:200]}


def _check_storage(settings: Any) -> dict[str, object]:
    root = getattr(settings.storage, "root", None)
    if root is None:
        return {"writable": False, "reason": "not_configured"}
    try:
        import tempfile

        with tempfile.NamedTemporaryFile(dir=root, delete=True):
            return {"writable": True, "path": str(root)}
    except Exception as exc:
        return {"writable": False, "reason": str(exc)[:200]}


@router.get("/healthz")
async def healthz(request: Request, deep: bool = False) -> dict[str, object]:
    out: dict[str, object] = {
        "status": "ok",
        "time": datetime.now(UTC).isoformat(),
    }
    settings = getattr(request.app.state, "settings", None)
    if settings is None or not getattr(
        settings.observability, "healthz_omit_version", False,
    ):
        out["service"] = "zammad-pdf-archiver"
        out["version"] = _service_version()

    if deep and settings is not None:
        checks: dict[str, object] = {}
        checks["redis"] = await _check_redis(settings)
        checks["storage"] = _check_storage(settings)
        out["checks"] = checks
        all_ok = all(
            v.get("available", v.get("writable", False))
            for v in checks.values()
            if isinstance(v, dict) and "reason" not in v
        )
        if not all_ok:
            out["status"] = "degraded"

    return out
