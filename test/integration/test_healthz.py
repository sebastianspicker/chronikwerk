from __future__ import annotations

# pylint: disable=wrong-import-order
# ruff: noqa: I001  # Pylint and Ruff classify the in-repository test package differently.

from fastapi.testclient import TestClient

from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.server import create_app


def test_healthz_ok(tmp_path) -> None:
    response = TestClient(create_app(make_settings(str(tmp_path)))).get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_healthz_deep_checks_storage(tmp_path) -> None:
    response = TestClient(create_app(make_settings(str(tmp_path)))).get("/healthz?deep=true")
    body = response.json()
    assert response.status_code == 200
    assert body["checks"]["storage"]["writable"] is True


def test_healthz_deep_storage_failure_uses_stable_reason(tmp_path) -> None:
    missing_root = tmp_path / "does-not-exist"
    settings = make_settings(str(missing_root))
    response = TestClient(create_app(settings)).get("/healthz?deep=true")
    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "degraded"
    assert body["checks"]["storage"] == {
        "writable": False,
        "reason": "storage_unavailable",
    }
    assert str(missing_root) not in response.text
