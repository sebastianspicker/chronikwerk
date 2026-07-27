"""Verifies dotenv loading and its precedence beneath explicit configuration."""

from __future__ import annotations

from pathlib import Path

import pytest

from chronikwerk.config.load import load_settings
from chronikwerk.config.settings import Settings


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear configuration variables so each test starts isolated."""
    keys = [
        "CONFIG_PATH",
        "ZAMMAD__BASE_URL",
        "ZAMMAD__API_TOKEN",
        "STORAGE__ROOT",
        "ZAMMAD__WEBHOOK_HMAC_SECRET",
    ]
    for key in keys:
        monkeypatch.delenv(key, raising=False)


def _write_config(tmp_path: Path, filename: str, *, server_port: int | None) -> Path:
    """Write the minimal valid YAML configuration for precedence assertions."""
    server_lines = [] if server_port is None else ["server:", f"  port: {server_port}"]
    config = tmp_path / filename
    config.write_text(
        "\n".join(
            [
                *server_lines,
                "zammad:",
                "  base_url: https://zammad.example.local",
                "  api_token: yaml-token",
                "  webhook_hmac_secret: yaml-webhook-hmac-secret-0123456789abcdef",
                "storage:",
                f"  root: {tmp_path}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return config


def _settings_with_file_secret(secrets: Path, tmp_path: Path) -> Settings:
    """Build settings whose retry token can resolve from dotenv or process environment."""
    return Settings(  # type: ignore[call-arg]
        _secrets_dir=secrets,
        zammad={  # type: ignore[arg-type]
            "base_url": "https://zammad.example.local",
            "api_token": "token",
            "webhook_hmac_secret": "secret",
        },
        storage={"root": tmp_path},  # type: ignore[arg-type]
    )


def test_dotenv_file_is_loaded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    _clear_env(monkeypatch)

    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "ZAMMAD__BASE_URL=https://zammad.example.local",
                "ZAMMAD__API_TOKEN=test-token",
                "ZAMMAD__WEBHOOK_HMAC_SECRET=test-webhook-hmac-secret-0123456789abcdef",
                f"STORAGE__ROOT={tmp_path.as_posix()}",
                "",
            ]
        ),
        encoding="utf-8",
    )

    settings = load_settings()
    assert str(settings.zammad.base_url).rstrip("/") == "https://zammad.example.local"
    assert settings.zammad.api_token.get_secret_value() == "test-token"
    assert settings.storage.root == tmp_path


def test_settings_precedence_env_then_yaml_then_dotenv_then_file_secret(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    _clear_env(monkeypatch)
    config = _write_config(tmp_path, "config.yaml", server_port=8101)
    (tmp_path / ".env").write_text("SERVER__PORT=8202\n", encoding="utf-8")

    monkeypatch.setenv("SERVER__PORT", "8303")
    assert load_settings(config_path=config).server.port == 8303
    monkeypatch.delenv("SERVER__PORT")
    # A complete YAML/init section wins over the lower-priority dotenv source.
    assert load_settings(config_path=config).server.port == 8101
    config_without_server = _write_config(tmp_path, "config-without-server.yaml", server_port=None)
    assert load_settings(config_path=config_without_server).server.port == 8202
    (tmp_path / ".env").unlink()
    assert load_settings(config_path=config_without_server).server.port == 8080

    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "RETRY_BEARER_TOKEN").write_text("file-token\n", encoding="utf-8")
    (tmp_path / ".env").write_text("RETRY_BEARER_TOKEN=dotenv-token\n", encoding="utf-8")
    settings = _settings_with_file_secret(secrets, tmp_path)
    assert settings.retry_bearer_token is not None
    assert settings.retry_bearer_token.get_secret_value() == "dotenv-token"
    monkeypatch.setenv("RETRY_BEARER_TOKEN", "process-token")
    settings = _settings_with_file_secret(secrets, tmp_path)
    assert settings.retry_bearer_token is not None
    assert settings.retry_bearer_token.get_secret_value() == "process-token"
