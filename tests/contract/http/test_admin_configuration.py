"""Verify authenticated administration configuration persistence over HTTP."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from chronikwerk.configuration.revisions import overlay_from_flat
from chronikwerk.web.app import create_app
from tests.support.settings_factory import make_settings


def _client(tmp_path) -> TestClient:
    """Create an enabled administration client with isolated durable state."""
    settings = make_settings(
        str(tmp_path),
        secret="test-webhook-secret-0123456789abcdef",
        overrides={
            "admin": {
                "enabled": True,
                "access_token": "admin-token-0123456789abcdef0123456789",
                "state_dir": str(tmp_path / "admin-state"),
            }
        },
    )
    return TestClient(create_app(settings), base_url="https://testserver")


def _login_and_csrf(client: TestClient) -> str:
    """Authenticate and return the session's anti-forgery token."""
    login = client.post(
        "/admin/login",
        data={
            "access_token": "admin-token-0123456789abcdef0123456789",
            "next": "/admin/configuration",
        },
        follow_redirects=False,
    )
    assert login.status_code == 303
    page = client.get("/admin/configuration")
    match = re.search(r'<meta name="csrf-token" content="([^"]+)">', page.text)
    assert match is not None
    return match.group(1)


def test_admin_api_validates_stages_and_reports_restart_state(tmp_path) -> None:
    client = _client(tmp_path)
    csrf = _login_and_csrf(client)

    before = client.get("/admin/api/v1/config")
    assert before.status_code == 200
    revision = before.json()["revision"]
    assert before.json()["restart_required"] is False

    preview = client.post(
        "/admin/api/v1/config/validate",
        headers={"X-CSRF-Token": csrf},
        json={"values": {"workflow.trigger_tag": "archive:next"}},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["diff"] == [
        {"path": "workflow.trigger_tag", "before": "pdf:sign", "after": "archive:next"}
    ]

    staged = client.put(
        "/admin/api/v1/config/staged",
        headers={"X-CSRF-Token": csrf, "If-Match": revision},
        json={"overlay": {"pdf": {"locale": "en-GB"}}},
    )
    assert staged.status_code == 200
    assert staged.json()["restart_required"] is True

    after = client.get("/admin/api/v1/config").json()
    assert after["staged_revision"] == staged.json()["revision"]
    assert after["restart_required"] is True

    conflict = client.put(
        "/admin/api/v1/config/staged",
        headers={"X-CSRF-Token": csrf, "If-Match": revision},
        json={"overlay": {"pdf": {"locale": "de-DE"}}},
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "config_revision_conflict"


def test_admin_config_rejects_security_changes_without_acknowledgement(tmp_path) -> None:
    client = _client(tmp_path)
    csrf = _login_and_csrf(client)

    response = client.post(
        "/admin/api/v1/config/validate",
        headers={"X-CSRF-Token": csrf},
        json={"values": {"hardening.transport.trust_env": True}},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "security_acknowledgement_required"


@pytest.mark.parametrize(
    ("path", "canonical", "legacy", "value"),
    [
        ("zammad.timeout_seconds", "ZAMMAD_TIMEOUT_SECONDS", "ZAMMAD__TIMEOUT_SECONDS", 20),
        (
            "hardening.transport.trust_env",
            "ZAMMAD_TRUST_ENV",
            "HARDENING__TRANSPORT__TRUST_ENV",
            False,
        ),
        (
            "hardening.transport.allow_private_networks",
            "ZAMMAD_ALLOW_PRIVATE_ORIGIN",
            "HARDENING__TRANSPORT__ALLOW_PRIVATE_NETWORKS",
            False,
        ),
    ],
)
@pytest.mark.parametrize("use_canonical", [False, True])
def test_alias_owned_fields_are_locked_for_display_validation_and_staging(
    tmp_path, monkeypatch, path, canonical, legacy, value, use_canonical
) -> None:
    monkeypatch.delenv(canonical, raising=False)
    monkeypatch.delenv(legacy, raising=False)
    monkeypatch.setenv(canonical if use_canonical else legacy, str(value))
    client = _client(tmp_path)
    csrf = _login_and_csrf(client)
    config = client.get("/admin/api/v1/config").json()
    field = next(field for field in config["fields"] if field["path"] == path)
    assert field["source"] == "environment"
    assert field["editable"] is False
    payload = {"values": {path: value}, "security_acknowledged": True}
    validation = client.post(
        "/admin/api/v1/config/validate", headers={"X-CSRF-Token": csrf}, json=payload
    )
    overlay = overlay_from_flat({path: value})
    staging = client.put(
        "/admin/api/v1/config/staged",
        headers={"X-CSRF-Token": csrf, "If-Match": config["revision"]},
        json={"overlay": overlay, "security_acknowledged": True},
    )
    for response in (validation, staging):
        assert response.status_code == 422
        assert response.json()["code"] == "environment_owned_field"
    assert client.get("/admin/api/v1/config").json()["revision"] == config["revision"]
