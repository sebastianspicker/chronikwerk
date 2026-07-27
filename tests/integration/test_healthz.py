"""Verifies shallow and deep health checks, including single-flight storage probes."""

from __future__ import annotations

import asyncio

import httpx
from fastapi.testclient import TestClient

from chronikwerk.app.routes import healthz as healthz_module
from chronikwerk.app.server import create_app
from tests.support.settings_factory import make_settings


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


def test_healthz_deep_check_is_single_flight(tmp_path, monkeypatch) -> None:
    async def _run() -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def blocked_deep_check(_settings):
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return {"storage": {"writable": True}}, True

        monkeypatch.setattr(healthz_module, "_deep_checks", blocked_deep_check)
        app = create_app(make_settings(str(tmp_path)))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first = asyncio.create_task(client.get("/healthz?deep=true"))
            await started.wait()
            second = asyncio.create_task(client.get("/healthz?deep=true"))
            try:
                response = await asyncio.wait_for(second, timeout=1.0)
                assert response.status_code == 503
                assert response.json()["code"] == "deep_health_check_busy"
                assert calls == 1
            finally:
                release.set()
                await asyncio.gather(first, second, return_exceptions=True)

    asyncio.run(_run())
