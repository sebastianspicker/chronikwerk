from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, Request

from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.domain.package_version import get_package_version

router = APIRouter()
log = structlog.get_logger(__name__)


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
            stat = os.statvfs(root)
            return {"writable": True, "free_bytes": int(stat.f_bavail * stat.f_frsize)}
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
    out = _base_health_response()
    settings = getattr(request.app.state, "settings", None)
    _add_version_info(out, settings)

    if deep and settings is None:
        return _settings_not_loaded_response(out)

    if deep and settings is not None:
        out.update(await _deep_health_fields(settings))

    return out


def _base_health_response() -> dict[str, object]:
    return {
        "status": "ok",
        "time": datetime.now(UTC).isoformat(),
    }


def _add_version_info(out: dict[str, object], settings: Settings | None) -> None:
    if settings is not None and not settings.observability.healthz_omit_version:
        out["service"] = "zammad-pdf-archiver"
        out["version"] = get_package_version("zammad-pdf-archiver", fallback="0.0.0")


def _settings_not_loaded_response(out: dict[str, object]) -> dict[str, object]:
    out["status"] = "degraded"
    out["reason"] = "settings_not_loaded"
    out["checks"] = {}
    return out


async def _deep_health_fields(settings: Settings) -> dict[str, object]:
    checks: dict[str, object] = {
        "redis": await _check_redis(settings),
        "storage": _check_storage(settings),
    }
    status = "ok" if _deep_checks_ok(checks) else "degraded"
    return {"status": status, "checks": checks}


def _deep_checks_ok(checks: dict[str, object]) -> bool:
    healthy_checks = [
        result
        for name, value in checks.items()
        if (result := _deep_check_healthy(name, value)) is not None
    ]
    return bool(healthy_checks) and all(healthy_checks)
