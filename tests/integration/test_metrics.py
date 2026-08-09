"""Verifies metrics exposure, bearer protection, and ingest counter updates."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import threading
import time

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from chronikwerk.app.jobs import _ticket_pipeline as ticket_pipeline_module
from chronikwerk.app.jobs.shutdown import clear_shutting_down
from chronikwerk.app.jobs.ticket_storage import StorageResult
from chronikwerk.app.server import create_app
from chronikwerk.config.settings import Settings
from chronikwerk.domain.state_machine import TRIGGER_TAG
from tests.support.settings_factory import make_settings
from tests.support.zammad_fixtures import (
    archived_ticket_json,
    register_archive_mutation_routes,
)

_TEST_WEBHOOK_SECRET = "test-webhook-secret"
_TEST_METRICS_TOKEN = "test-metrics-bearer-token-0123456789abcdef"
_METRICS_HEADERS = {"Authorization": f"Bearer {_TEST_METRICS_TOKEN}"}


def _test_settings(storage_root: str) -> Settings:
    """Build settings isolated to this test scenario."""
    return make_settings(
        storage_root,
        secret=_TEST_WEBHOOK_SECRET,
        overrides={
            "observability": {
                "metrics_enabled": True,
                "metrics_bearer_token": _TEST_METRICS_TOKEN,
            }
        },
    )


def _test_settings_metrics_disabled(storage_root: str) -> Settings:
    """Build settings that exercise the metrics-disabled branch."""
    return make_settings(storage_root)


_METRIC_LINE_RE = re.compile(r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*) (?P<value>[-+0-9.eE]+)$")


def _metric_value(text: str, name: str) -> float:
    """Extract one Prometheus metric value for precise assertions."""
    for line in text.splitlines():
        match = _METRIC_LINE_RE.match(line)
        if match and match.group("name") == name:
            return float(match.group("value"))
    raise AssertionError(f"metric {name!r} not found in /metrics output")


def _register_ingest_routes() -> None:
    """Stub an empty-article ingest so metrics observe the complete job path."""
    respx.get("https://zammad.example.local/api/v1/tickets/123").mock(
        return_value=httpx.Response(200, json=archived_ticket_json(archive_path=["A", "B", "C"]))
    )
    respx.get(
        "https://zammad.example.local/api/v1/tags",
        params={"object": "Ticket", "o_id": "123"},
    ).mock(return_value=httpx.Response(200, json=[TRIGGER_TAG]))
    respx.get("https://zammad.example.local/api/v1/ticket_articles/by_ticket/123").mock(
        return_value=httpx.Response(200, json=[])
    )
    register_archive_mutation_routes()


def _ingest_body() -> bytes:
    """Return a representative ingest request body."""
    payload = {"ticket": {"id": 123}, "user": {"login": "agent-from-webhook"}}
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _signed_ingest_headers(body: bytes) -> dict[str, str]:
    """Build signed headers for an ingest request."""
    digest = hmac.new(_TEST_WEBHOOK_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Hub-Signature": f"sha256={digest}",
        "X-Zammad-Delivery": "delivery-metrics-20260207-0001",
    }


def _metrics_client(tmp_path, token: str | None) -> TestClient:
    """Build a metrics-enabled client for bearer-token scenarios."""
    settings = Settings.from_mapping(
        {
            "zammad": {"base_url": "https://zammad.example.local", "api_token": "test-token"},
            "storage": {"root": str(tmp_path)},
            "observability": {
                "metrics_enabled": True,
                "metrics_bearer_token": token,
            },
            "hardening": {
                "webhook": {},
                "transport": {"allow_private_networks": True},
            },
        }
    )
    return TestClient(create_app(settings))


def test_metrics_endpoint_returns_prometheus_text(tmp_path) -> None:
    app = create_app(_test_settings(str(tmp_path)))
    client = TestClient(app)

    resp = client.get("/metrics", headers=_METRICS_HEADERS)
    assert resp.status_code == 200
    assert "text/plain" in resp.headers.get("content-type", "")
    assert "processed_total" in resp.text


def test_metrics_endpoint_is_not_exposed_when_disabled(tmp_path) -> None:
    app = create_app(_test_settings_metrics_disabled(str(tmp_path)))
    client = TestClient(app)

    resp = client.get("/metrics")
    assert resp.status_code == 404


def test_metrics_requires_bearer_when_configured(tmp_path) -> None:
    client = _metrics_client(tmp_path, "secret-token")

    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert (
        client.get("/metrics", headers={"Authorization": "Bearer secret-token"}).status_code == 200
    )


@pytest.mark.parametrize("token", [None, "", "   "])
def test_metrics_fails_closed_when_token_is_missing_or_empty(tmp_path, token) -> None:
    client = _metrics_client(tmp_path, token)

    assert client.get("/metrics", headers={"Authorization": "Bearer  "}).status_code == 503


def test_ingest_success_increments_processed_total(tmp_path, monkeypatch) -> None:
    clear_shutting_down()
    app = create_app(_test_settings(str(tmp_path)))
    body = _ingest_body()
    completed = threading.Event()

    async def render_and_store_stub(**_kwargs) -> StorageResult:
        target = tmp_path / "archive" / "Ticket-20240123.pdf"
        result = StorageResult(
            target_path=target,
            sidecar_path=target.with_suffix(".pdf.json"),
            sha256_hex="0" * 64,
            size_bytes=4,
        )
        completed.set()
        return result

    monkeypatch.setattr(ticket_pipeline_module, "render_and_store_ticket", render_and_store_stub)

    with TestClient(app) as client:
        before = _metric_value(
            client.get("/metrics", headers=_METRICS_HEADERS).text,
            "processed_total",
        )
        with respx.mock:
            _register_ingest_routes()
            resp = client.post(
                "/ingest",
                content=body,
                headers=_signed_ingest_headers(body),
            )
            assert resp.status_code == 202
            assert completed.wait(timeout=5.0), "background processing did not reach terminal stub"
            deadline = time.monotonic() + 5.0
            after = _metric_value(
                client.get("/metrics", headers=_METRICS_HEADERS).text,
                "processed_total",
            )
            while after != before + 1.0 and time.monotonic() < deadline:
                after = _metric_value(
                    client.get("/metrics", headers=_METRICS_HEADERS).text,
                    "processed_total",
                )

        assert after == before + 1.0
    clear_shutting_down()
