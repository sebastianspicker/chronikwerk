from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from importlib import metadata

from fastapi import APIRouter, Request

from zammad_pdf_archiver.config.settings import Settings

router = APIRouter()


def _service_version() -> str:
    try:
        return metadata.version("zammad-pdf-archiver")
    except metadata.PackageNotFoundError:
        return "0.0.0"



def _check_storage(settings: Settings) -> dict[str, object]:
    root = settings.storage.root
    try:
        with tempfile.NamedTemporaryFile(dir=root, delete=True):
            return {"writable": True}
    except OSError as exc:
        return {"writable": False, "reason": str(exc)[:200]}


def _deep_check_healthy(_name: str, result: object) -> bool | None:
    if not isinstance(result, dict):
        return None
    if "available" in result:
        return bool(result["available"])
    if "writable" in result:
        return bool(result["writable"])
    return None


async def _deep_checks(settings: Settings) -> tuple[dict[str, object], bool]:
    checks: dict[str, object] = {}
    checks["storage"] = _check_storage(settings)
    healthy_checks = [
        result
        for name, value in checks.items()
        if (result := _deep_check_healthy(name, value)) is not None
    ]
    return checks, bool(healthy_checks) and all(healthy_checks)


@router.get("/healthz")
async def healthz(request: Request, deep: bool = False) -> dict[str, object]:
    """Return service health; include storage check when deep=True."""
    out: dict[str, object] = {
        "status": "ok",
        "time": datetime.now(UTC).isoformat(),
    }
    settings = getattr(request.app.state, "settings", None)
    if settings is None or not settings.observability.healthz_omit_version:
        out["service"] = "zammad-pdf-archiver"
        out["version"] = _service_version()

    if deep and settings is not None:
        checks, all_ok = await _deep_checks(settings)
        out["checks"] = checks
        if not all_ok:
            out["status"] = "degraded"

    return out
