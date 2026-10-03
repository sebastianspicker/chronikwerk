"""Verify production administration pages expose configuration truthfully."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from chronikwerk.web.app import create_app
from tests.support.settings_factory import make_settings


def _overview(
    tmp_path,
    *,
    locale: str,
    signing_enabled: bool,
    timestamp_enabled: bool,
) -> str:
    """Render one authenticated overview for a concrete signing configuration."""
    signing: dict[str, object] = {
        "enabled": signing_enabled,
        "timestamp": {"enabled": timestamp_enabled},
    }
    if signing_enabled:
        signing["pfx_path"] = str(tmp_path / "synthetic-signer.pfx")
    if timestamp_enabled:
        signing["timestamp"] = {
            "enabled": True,
            "rfc3161": {"tsa_url": "https://tsa.example.test"},
        }
    settings = make_settings(
        str(tmp_path / "archive"),
        overrides={
            "admin": {
                "enabled": True,
                "access_token": "admin-token",
                "state_dir": str(tmp_path / "admin-state"),
            },
            "signing": signing,
        },
    )
    client = TestClient(create_app(settings), base_url="https://testserver")
    login = client.post(
        "/admin/login",
        data={"access_token": "admin-token", "locale": locale},
        follow_redirects=False,
    )
    assert login.status_code == 303
    response = client.get("/admin/")
    assert response.status_code == 200
    return response.text


@pytest.mark.parametrize(
    ("locale", "enabled", "disabled"),
    [("en-GB", "Enabled", "Disabled"), ("de-DE", "Aktiviert", "Deaktiviert")],
)
@pytest.mark.parametrize(
    ("signing_enabled", "timestamp_enabled"),
    [(False, False), (True, False), (True, True)],
)
def test_overview_reports_configured_signing_and_timestamp_states(
    tmp_path,
    locale: str,
    enabled: str,
    disabled: str,
    signing_enabled: bool,
    timestamp_enabled: bool,
) -> None:
    html = _overview(
        tmp_path,
        locale=locale,
        signing_enabled=signing_enabled,
        timestamp_enabled=timestamp_enabled,
    )

    signing = re.search(r"<dd data-signing-state>([^<]+)</dd>", html)
    timestamp = re.search(r"<dd data-timestamp-state>([^<]+)</dd>", html)

    assert signing is not None
    assert timestamp is not None
    assert signing.group(1) == (enabled if signing_enabled else disabled)
    assert timestamp.group(1) == (enabled if timestamp_enabled else disabled)
