from __future__ import annotations

from pathlib import Path

import pytest

from zammad_pdf_archiver.config.load import load_settings
from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.config.validate import ConfigValidationError, validate_settings


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "CONFIG_PATH",
        "ZAMMAD__BASE_URL",
        "ZAMMAD__API_TOKEN",
        "ZAMMAD__WEBHOOK_HMAC_SECRET",
        "ZAMMAD__ZAMMAD__WEBHOOK_HMAC_SECRET",
        "STORAGE__ROOT",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def _clear_env_autouse(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)


def test_yaml_loading_works(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "zammad:",
                "  base_url: https://zammad.example.local",
                "  api_token: test-token",
                "  webhook_hmac_secret: test-secret",
                "storage:",
                "  root: /mnt/archive",
                "",
            ]
        ),
        encoding="utf-8",
    )

    settings = load_settings(config_path=config_path)

    assert str(settings.zammad.base_url).rstrip("/") == "https://zammad.example.local"
    assert settings.zammad.api_token.get_secret_value() == "test-token"
    assert settings.storage.root.as_posix() == "/mnt/archive"


def test_env_overrides_yaml(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "zammad:",
                "  base_url: https://zammad.from-yaml.local",
                "  api_token: yaml-token",
                "  webhook_hmac_secret: yaml-secret",
                "storage:",
                "  root: /mnt/archive",
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ZAMMAD__BASE_URL", "https://zammad.from-env.local")
    monkeypatch.setenv("ZAMMAD__API_TOKEN", "env-token")

    settings = load_settings(config_path=config_path)

    assert str(settings.zammad.base_url).rstrip("/") == "https://zammad.from-env.local"
    assert settings.zammad.api_token.get_secret_value() == "env-token"


def test_validate_settings_requires_webhook_secret() -> None:
    settings = Settings.from_mapping(
        {
            "zammad": {"base_url": "https://zammad.example.local", "api_token": "t"},
            "storage": {"root": "/mnt/archive"},
        }
    )

    with pytest.raises(ConfigValidationError) as exc:
        validate_settings(settings)

    assert "zammad.webhook_hmac_secret" in str(exc.value)


def test_validate_settings_rejects_plain_http_and_disabled_tls() -> None:
    settings = Settings.from_mapping(
        {
            "zammad": {
                "base_url": "http://zammad.example.local",
                "api_token": "t",
                "webhook_hmac_secret": "secret",
                "verify_tls": False,
            },
            "storage": {"root": "/mnt/archive"},
        }
    )

    with pytest.raises(ConfigValidationError) as exc:
        validate_settings(settings)

    msg = str(exc.value)
    assert "zammad.base_url" in msg
    assert "zammad.verify_tls" in msg


def test_validate_settings_accepts_secure_minimum() -> None:
    settings = Settings.from_mapping(
        {
            "zammad": {
                "base_url": "https://zammad.example.local",
                "api_token": "t",
                "webhook_hmac_secret": "secret",
            },
            "storage": {"root": "/mnt/archive"},
        }
    )

    validate_settings(settings)
