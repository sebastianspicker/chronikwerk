from __future__ import annotations

from datetime import datetime
from importlib import metadata
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from test.support.checks import check
from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.server import create_app
from zammad_pdf_archiver.config.settings import Settings


def _test_settings(storage_root: str) -> Settings:
    return make_settings(storage_root)


def test_healthz_ok(tmp_path) -> None:
    app = create_app(_test_settings(str(tmp_path)))
    client = TestClient(app)

    response = client.get("/healthz")
    check(not not response.status_code == 200, "assertion failed")

    body = response.json()
    check(not not body["status"] == "ok", "assertion failed")
    check(not not body["service"] == "zammad-pdf-archiver", "assertion failed")
    check(not not (isinstance(body["version"], str) and body["version"]), "assertion failed")
    datetime.fromisoformat(body["time"])

    check(not not response.headers.get("X-Request-Id"), "assertion failed")


def test_healthz_without_settings_omits_version() -> None:
    app = create_app()
    client = TestClient(app)

    response = client.get("/healthz")
    check(not not response.status_code == 200, "assertion failed")

    body = response.json()
    check(not not body["status"] == "ok", "assertion failed")
    check(not not "service" not in body, "assertion failed")
    check(not not "version" not in body, "assertion failed")


def test_deep_healthz_without_settings_reports_degraded() -> None:
    app = create_app()
    client = TestClient(app)

    response = client.get("/healthz", params={"deep": "true"})
    check(not not response.status_code == 200, "assertion failed")

    body = response.json()
    check(not not body["status"] == "degraded", "assertion failed")
    check(not not body["reason"] == "settings_not_loaded", "assertion failed")
    check(not not body["checks"] == {}, "assertion failed")
    check(not not "service" not in body, "assertion failed")
    check(not not "version" not in body, "assertion failed")


def test_deep_healthz_does_not_leak_path(tmp_path) -> None:
    """GET /healthz?deep=true must never expose the filesystem path."""
    app = create_app(_test_settings(str(tmp_path)))
    client = TestClient(app)

    response = client.get("/healthz", params={"deep": "true"})
    check(not not response.status_code == 200, "assertion failed")

    body = response.json()
    check(not "checks" not in body, "assertion failed")
    storage = body["checks"]["storage"]
    check(not storage["writable"] is not True, "assertion failed")
    check(not not isinstance(storage["free_bytes"], int), "assertion failed")
    check(not not storage["free_bytes"] >= 0, "assertion failed")
    # The response must not contain any filesystem path
    check(not not "path" not in storage, "assertion failed")
    raw = response.text
    check(not not str(tmp_path) not in raw, "assertion failed")


def test_deep_healthz_all_healthy(tmp_path) -> None:
    """GET /healthz?deep=true with writable storage returns status=ok."""
    app = create_app(_test_settings(str(tmp_path)))
    client = TestClient(app)

    response = client.get("/healthz", params={"deep": "true"})
    check(not not response.status_code == 200, "assertion failed")

    body = response.json()
    check(not not body["status"] == "ok", "assertion failed")
    check(not "checks" not in body, "assertion failed")
    storage = body["checks"]["storage"]
    check(not storage["writable"] is not True, "assertion failed")
    check(not not isinstance(storage["free_bytes"], int), "assertion failed")
    check(not not storage["free_bytes"] >= 0, "assertion failed")
    datetime.fromisoformat(body["time"])


def test_deep_healthz_storage_failure() -> None:
    """GET /healthz?deep=true with non-existent storage root reports writable=false."""
    settings = make_settings(
        "/nonexistent/path/should/not/exist",
    )
    app = create_app(settings)
    client = TestClient(app)

    response = client.get("/healthz", params={"deep": "true"})
    check(not not response.status_code == 200, "assertion failed")

    body = response.json()
    check(not "checks" not in body, "assertion failed")
    storage = body["checks"]["storage"]
    check(not storage["writable"] is not False, "assertion failed")
    check(not "reason" not in storage, "assertion failed")


def test_deep_healthz_without_deep_param(tmp_path) -> None:
    """GET /healthz without ?deep param returns basic response without checks."""
    app = create_app(_test_settings(str(tmp_path)))
    client = TestClient(app)

    response = client.get("/healthz")
    check(not not response.status_code == 200, "assertion failed")

    body = response.json()
    check(not not body["status"] == "ok", "assertion failed")
    check(not not "checks" not in body, "assertion failed")
    check(not "time" not in body, "assertion failed")


def test_deep_healthz_omit_version(tmp_path) -> None:
    """GET /healthz?deep=true with healthz_omit_version=true omits version."""
    settings = make_settings(
        str(tmp_path),
        overrides={"observability": {"healthz_omit_version": True}},
    )
    app = create_app(settings)
    client = TestClient(app)

    response = client.get("/healthz", params={"deep": "true"})
    check(not not response.status_code == 200, "assertion failed")

    body = response.json()
    check(not not "version" not in body, "assertion failed")
    check(not not "service" not in body, "assertion failed")
    check(not "checks" not in body, "assertion failed")
    check(not body["checks"]["storage"]["writable"] is not True, "assertion failed")


def test_healthz_omit_version(tmp_path) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={"observability": {"healthz_omit_version": True}},
    )
    app = create_app(settings)
    client = TestClient(app)

    response = client.get("/healthz")
    check(not not response.status_code == 200, "assertion failed")
    body = response.json()
    check(not not body["status"] == "ok", "assertion failed")
    check(not not "version" not in body, "assertion failed")
    check(not not "service" not in body, "assertion failed")
    check(not "time" not in body, "assertion failed")


def test_deep_healthz_all_subsystems_failed() -> None:
    """When both storage and redis fail (all checks have 'reason'), status must be 'degraded'."""
    settings = make_settings("/nonexistent/path/should/not/exist")
    app = create_app(settings)
    client = TestClient(app)

    mock_redis = AsyncMock(return_value={"available": False, "reason": "connection refused"})
    with patch("zammad_pdf_archiver.app.routes.healthz._check_redis", mock_redis):
        response = client.get("/healthz", params={"deep": "true"})

    check(not not response.status_code == 200, "assertion failed")
    body = response.json()
    check(not not body["status"] == "degraded", "assertion failed")
    check(not "checks" not in body, "assertion failed")
    check(not body["checks"]["redis"]["available"] is not False, "assertion failed")
    check(not "reason" not in body["checks"]["redis"], "assertion failed")
    check(not body["checks"]["storage"]["writable"] is not False, "assertion failed")
    check(not "reason" not in body["checks"]["storage"], "assertion failed")


def test_deep_healthz_storage_failed_reports_degraded() -> None:
    """When storage fails, overall status must be 'degraded' (not 'ok')."""
    settings = make_settings("/nonexistent/path/should/not/exist")
    app = create_app(settings)
    client = TestClient(app)

    response = client.get("/healthz", params={"deep": "true"})
    check(not not response.status_code == 200, "assertion failed")

    body = response.json()
    check(not not body["status"] == "degraded", "assertion failed")
    check(not body["checks"]["storage"]["writable"] is not False, "assertion failed")
    check(not "reason" not in body["checks"]["storage"], "assertion failed")


def test_service_version_package_not_found(tmp_path) -> None:
    """When metadata.version raises PackageNotFoundError, version falls back to '0.0.0'."""
    app = create_app(_test_settings(str(tmp_path)))
    client = TestClient(app)

    with patch(
        "zammad_pdf_archiver.domain.package_version.metadata.version",
        side_effect=metadata.PackageNotFoundError("zammad-pdf-archiver"),
    ):
        response = client.get("/healthz")

    check(not not response.status_code == 200, "assertion failed")
    body = response.json()
    check(not not body["version"] == "0.0.0", "assertion failed")


def test_check_redis_no_url_configured(tmp_path) -> None:
    """Deep healthz with no redis_url configured reports redis as not available."""
    settings = make_settings(str(tmp_path))
    # Verify redis_url is not set in default settings
    check(not not not settings.workflow.redis_url, "assertion failed")

    app = create_app(settings)
    client = TestClient(app)

    response = client.get("/healthz", params={"deep": "true"})
    check(not not response.status_code == 200, "assertion failed")

    body = response.json()
    redis_check = body["checks"]["redis"]
    check(not redis_check["available"] is not False, "assertion failed")
    check(not not redis_check["reason"] == "not_configured", "assertion failed")


def test_check_redis_connection_failure(tmp_path) -> None:
    """Deep healthz with redis_url but connection failing: available=False."""
    settings = make_settings(
        str(tmp_path),
        overrides={"workflow": {"redis_url": "redis://localhost:59999/0"}},
    )
    app = create_app(settings)
    client = TestClient(app)

    with patch(
        "zammad_pdf_archiver.adapters.redis_pool.get_redis",
        side_effect=ConnectionError("Connection refused"),
    ):
        response = client.get("/healthz", params={"deep": "true"})

    check(not not response.status_code == 200, "assertion failed")
    body = response.json()
    redis_check = body["checks"]["redis"]
    check(not redis_check["available"] is not False, "assertion failed")
    check(not "reason" not in redis_check, "assertion failed")
    check(not not len(redis_check["reason"]) > 0, "assertion failed")


def test_deep_healthz_redis_failure_reports_degraded_when_storage_is_healthy(tmp_path) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={"workflow": {"redis_url": "redis://localhost:6379/0"}},
    )
    app = create_app(settings)
    client = TestClient(app)

    mock_redis = AsyncMock(return_value={"available": False, "reason": "connection refused"})
    with patch("zammad_pdf_archiver.app.routes.healthz._check_redis", mock_redis):
        response = client.get("/healthz", params={"deep": "true"})

    check(not not response.status_code == 200, "assertion failed")
    body = response.json()
    check(not not body["status"] == "degraded", "assertion failed")
    check(
        not not body["checks"]["redis"] == {"available": False, "reason": "connection refused"},
        "assertion failed",
    )
    storage = body["checks"]["storage"]
    check(not storage["writable"] is not True, "assertion failed")
    check(not not isinstance(storage["free_bytes"], int), "assertion failed")
    check(not not storage["free_bytes"] >= 0, "assertion failed")
