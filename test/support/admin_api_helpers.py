"""Shared helpers for admin API integration tests."""

from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from test.support.checks import check
from test.support.credentials import fake_credential
from test.support.integration_helpers import check_status_ok
from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.server import create_app


def admin_settings(storage_root: str):
    return make_settings(
        storage_root,
        overrides={
            "admin": {
                "enabled": True,
                "bearer_token": fake_credential("admin-token"),
                "history_limit": 25,
            }
        },
    )


def admin_client(tmp_path) -> TestClient:
    return TestClient(create_app(admin_settings(str(tmp_path))))


def admin_redis_app(tmp_path):
    return create_app(
        make_settings(
            str(tmp_path),
            overrides={
                "admin": {
                    "enabled": True,
                    "bearer_token": fake_credential("admin-token"),
                    "history_limit": 25,
                },
                "workflow": {"redis_url": "redis://localhost/0"},
            },
        )
    )


def admin_auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer admin-token"}


def basic_admin_headers() -> dict[str, str]:
    credentials = base64.b64encode(b"ignored:admin-token").decode("ascii")
    return {"Authorization": f"Basic {credentials}"}


def get_admin_history(client: TestClient):
    return client.get("/admin/api/history", headers=admin_auth_headers())


def assert_html_response(response) -> None:
    check_status_ok(response)
    content_type = response.headers.get("content-type", "")
    check(not "text/html" not in content_type, "assertion failed")
    check(
        not not ("<html" in response.text.lower() or "<!doctype" in response.text.lower()),
        "assertion failed",
    )


def stub_admin_replay(monkeypatch, *, selected: int, replayed: int, skipped: int) -> None:
    import zammad_pdf_archiver.app.routes.admin as admin_route

    async def _stub_replay(_settings, *, limit: int):  # noqa: ARG001
        return {
            "selected": selected,
            "replayed": replayed,
            "deleted": replayed,
            "skipped": skipped,
            "errors": 0,
            "not_deleted": 0,
        }

    monkeypatch.setattr(admin_route, "replay_dlq", _stub_replay)


def post_admin_replay(client: TestClient):
    return client.post(
        "/admin/api/dlq/replay",
        headers=admin_auth_headers(),
    )
