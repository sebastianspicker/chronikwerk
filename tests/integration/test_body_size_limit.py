"""Verifies oversized ingest requests are rejected before processing."""

from __future__ import annotations

from fastapi.testclient import TestClient

from chronikwerk.app.server import create_app
from chronikwerk.config.settings import Settings
from tests.support.http_security_test_helpers import assert_json_error, make_body_limit_settings


def _test_settings(storage_root: str) -> Settings:
    """Build settings isolated to this test scenario."""
    return make_body_limit_settings(
        storage_root,
        10,
        rate_limit={"enabled": False, "rps": 1, "burst": 1},
    )


def _post_oversized(storage_root: str, path: str, body: bytes):
    """Post an oversized body through one configured integration app."""
    client = TestClient(create_app(_test_settings(storage_root)))
    return client.post(
        path,
        content=body,
        headers={"Content-Type": "application/json"},
    )


def test_body_size_limit_triggers_on_ingest(tmp_path) -> None:
    resp = _post_oversized(str(tmp_path), "/ingest", b'{"ticket":{"id":123}}')
    assert_json_error(resp, status_code=413, code="request_too_large")
    assert resp.headers.get("X-Request-Id")


def test_body_size_limit_triggers_on_ingest_batch(tmp_path) -> None:
    resp = _post_oversized(str(tmp_path), "/ingest/batch", b'[{"ticket":{"id":123}}]')
    assert_json_error(resp, status_code=413, code="request_too_large")
    assert resp.headers.get("X-Request-Id")
