"""Verify required configuration and startup validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from chronikwerk.config.load import load_settings
from chronikwerk.config.validate import ConfigValidationError


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear configuration variables so each test starts isolated."""
    for key in (
        "ZAMMAD__BASE_URL",
        "ZAMMAD__API_TOKEN",
        "ZAMMAD__WEBHOOK_HMAC_SECRET",
        "STORAGE__ROOT",
    ):
        monkeypatch.delenv(key, raising=False)


def test_configuration_valid_yaml_loads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    config = tmp_path / "config.yaml"
    config.write_text(
        "\n".join(
            [
                "zammad:",
                "  base_url: https://zammad.example.local",
                "  api_token: test-token",
                "  webhook_hmac_secret: test-webhook-hmac-secret-0123456789abcdef",
                "storage:",
                f"  root: {tmp_path}",
                "",
            ]
        ),
        encoding="utf-8",
    )

    settings = load_settings(config_path=config)

    assert settings.storage.root == tmp_path


def test_configuration_missing_secret_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_env(monkeypatch)
    config = tmp_path / "config.yaml"
    config.write_text(
        "\n".join(
            [
                "zammad:",
                "  base_url: https://zammad.example.local",
                "  api_token: test-token",
                "storage:",
                f"  root: {tmp_path}",
                "",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigValidationError):
        load_settings(config_path=config)
