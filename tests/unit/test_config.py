"""Verifies YAML, environment, dotenv, and secret-file configuration precedence."""

from __future__ import annotations

from pathlib import Path

import pytest

from chronikwerk.config.load import load_settings
from chronikwerk.config.settings import Settings
from chronikwerk.config.validate import ConfigValidationError, validate_settings

_VALID_WEBHOOK_SECRET = "test-webhook-secret-0123456789abcdef"


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear configuration variables so each test starts isolated."""
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
    """Reset configuration variables before every test."""
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
                f"  webhook_hmac_secret: {_VALID_WEBHOOK_SECRET}",
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
                f"  webhook_hmac_secret: {_VALID_WEBHOOK_SECRET}",
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


def test_malformed_yaml_is_reported_as_safe_config_error(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    sensitive_fragment = "secret-value-that-must-not-leak"
    config_path.write_text(
        f"zammad:\n  api_token: [{sensitive_fragment}\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigValidationError) as exc:
        load_settings(config_path=config_path)

    message = str(exc.value)
    assert str(config_path) in message
    assert "Invalid YAML at line" in message
    assert sensitive_fragment not in message


def test_non_utf8_config_is_reported_as_safe_config_error(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_bytes(b"zammad:\n  api_token: \xff\n")

    with pytest.raises(ConfigValidationError) as exc:
        load_settings(config_path=config_path)

    message = str(exc.value)
    assert str(config_path) in message
    assert "Config file must be valid UTF-8" in message
    assert "0xff" not in message


@pytest.mark.parametrize("contents", ["", "null\n"])
def test_empty_yaml_is_treated_as_an_empty_configuration(contents: str, tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(contents, encoding="utf-8")

    with pytest.raises(ConfigValidationError) as exc:
        load_settings(config_path=config_path)

    assert "zammad.base_url" in str(exc.value)
    assert "storage.root" in str(exc.value)


def test_yaml_root_must_be_a_mapping(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("- not-a-configuration\n", encoding="utf-8")

    with pytest.raises(ConfigValidationError, match="YAML root must be a mapping/object"):
        load_settings(config_path=config_path)


def test_unreadable_yaml_reports_path_without_contents(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("zammad: {}\n", encoding="utf-8")

    def fail_read(_path: Path, **_kwargs: object) -> str:
        raise OSError("permission denied")

    monkeypatch.setattr("chronikwerk.config.load.Path.read_text", fail_read)
    with pytest.raises(ConfigValidationError) as exc:
        load_settings(config_path=config_path)

    assert str(config_path) in str(exc.value)
    assert "Unable to read config file" in str(exc.value)


def test_invalid_canonical_alias_is_reported_as_conflict_without_echoing_value(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "zammad:",
                "  base_url: https://zammad.example.local",
                "  api_token: test-token",
                f"  webhook_hmac_secret: {_VALID_WEBHOOK_SECRET}",
                "storage:",
                f"  root: {tmp_path / 'archive'}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    secret_like_invalid_value = "not-a-valid-origin-with-secret"
    monkeypatch.setenv("ZAMMAD_ORIGIN", secret_like_invalid_value)
    monkeypatch.setenv("ZAMMAD__BASE_URL", "https://zammad.example.local")

    with pytest.raises(ConfigValidationError) as exc:
        load_settings(config_path=config_path)

    assert "ZAMMAD_ORIGIN" in str(exc.value)
    assert secret_like_invalid_value not in str(exc.value)


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


def test_validate_settings_rejects_url_credentials_without_leaking_them() -> None:
    secret = "super-secret-password"
    settings = Settings.from_mapping(
        {
            "zammad": {
                "base_url": f"https://alice:{secret}@zammad.example.local",
                "api_token": "t",
                "webhook_hmac_secret": _VALID_WEBHOOK_SECRET,
            },
            "storage": {"root": "/mnt/archive"},
        }
    )

    with pytest.raises(ConfigValidationError) as exc:
        validate_settings(settings)

    message = str(exc.value)
    assert "must not include credentials" in message
    assert secret not in message


def test_validate_settings_accepts_secure_minimum() -> None:
    settings = Settings.from_mapping(
        {
            "zammad": {
                "base_url": "https://zammad.example.local",
                "api_token": "t",
                "webhook_hmac_secret": _VALID_WEBHOOK_SECRET,
            },
            "storage": {"root": "/mnt/archive"},
        }
    )

    validate_settings(settings)


@pytest.mark.parametrize(
    "secret",
    [
        "CHANGE-ME",
        "x" * 31,
        "CHANGE-ME-AT-LEAST-32-CHARACTERS",
        f" {_VALID_WEBHOOK_SECRET} ",
    ],
)
def test_validate_settings_rejects_weak_or_placeholder_webhook_secrets(secret: str) -> None:
    settings = Settings.from_mapping(
        {
            "zammad": {
                "base_url": "https://zammad.example.local",
                "api_token": "t",
                "webhook_hmac_secret": secret,
            },
            "storage": {"root": "/mnt/archive"},
        }
    )

    with pytest.raises(ConfigValidationError) as exc:
        validate_settings(settings)

    assert "zammad.webhook_hmac_secret" in str(exc.value)
