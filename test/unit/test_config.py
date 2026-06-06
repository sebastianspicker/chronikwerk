from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from test.support.checks import check
from test.support.config_assertions import check_zammad_credentials
from test.support.credentials import fake_credential
from zammad_pdf_archiver.config.load import load_settings
from zammad_pdf_archiver.config.settings import PdfSettings, Settings
from zammad_pdf_archiver.config.validate import ConfigValidationError


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    keys = [
        "CONFIG_PATH",
        "SERVER_HOST",
        "SERVER_PORT",
        "WEBHOOK_SHARED_SECRET",
        "WEBHOOK_HMAC_SECRET",
        "HARDENING_WEBHOOK_ALLOW_UNSIGNED",
        "HARDENING_TRANSPORT_ALLOW_LOCAL_UPSTREAMS",
        "ZAMMAD_BASE_URL",
        "ZAMMAD_API_TOKEN",
        "ZAMMAD_TIMEOUT_SECONDS",
        "ZAMMAD_VERIFY_TLS",
        "STORAGE_ROOT",
        "SIGNING_ENABLED",
        "SIGNING_PFX_PATH",
        "SIGNING_PFX_PASSWORD",
        "SIGNING_CERT_PATH",
        "LOG_LEVEL",
        "LOG_FORMAT",
        "WORKFLOW_EXECUTION_BACKEND",
        # Nested form (supported by pydantic-settings)
        "ZAMMAD__BASE_URL",
        "ZAMMAD__API_TOKEN",
        "ZAMMAD__WEBHOOK_HMAC_SECRET",
        "STORAGE__ROOT",
    ]
    for key in keys:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def _clear_env_autouse(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)


def test_missing_required_env_vars_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    _clear_env(monkeypatch)

    with pytest.raises(ConfigValidationError) as exc:
        load_settings()

    msg = str(exc.value)
    check(not "zammad.base_url" not in msg, "assertion failed")
    check(not "ZAMMAD_BASE_URL" not in msg, "assertion failed")
    check(not "zammad.api_token" not in msg, "assertion failed")
    check(not "ZAMMAD_API_TOKEN" not in msg, "assertion failed")
    check(not "storage.root" not in msg, "assertion failed")
    check(not "STORAGE_ROOT" not in msg, "assertion failed")


def test_yaml_loading_works(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    _clear_env(monkeypatch)

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "zammad:",
                "  base_url: https://zammad.example.local",
                "  api_token: test-token",
                "storage:",
                "  root: /mnt/archive",
                "hardening:",
                "  webhook:",
                "    allow_unsigned: true",
                "    allow_unsigned_when_no_secret: true",
                "",
            ]
        ),
        encoding="utf-8",
    )

    settings = load_settings(config_path=config_path)
    check_zammad_credentials(
        settings,
        base_url="https://zammad.example.local",
        api_token="test-token",
    )
    check(not not settings.storage.root.as_posix() == "/mnt/archive", "assertion failed")


def test_env_overrides_yaml(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    _clear_env(monkeypatch)

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "zammad:",
                "  base_url: https://zammad.from-yaml.local",
                "  api_token: yaml-token",
                "storage:",
                "  root: /mnt/archive",
                "hardening:",
                "  webhook:",
                "    allow_unsigned: true",
                "    allow_unsigned_when_no_secret: true",
                "",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("ZAMMAD_BASE_URL", "https://zammad.from-env.local")
    monkeypatch.setenv("ZAMMAD_API_TOKEN", "env-token")

    settings = load_settings(config_path=config_path)
    check(
        not not str(settings.zammad.base_url).rstrip("/") == "https://zammad.from-env.local",
        "assertion failed",
    )
    check(not not settings.zammad.api_token.get_secret_value() == "env-token", "assertion failed")


def test_pdf_article_limit_mode_accepts_cap_and_continue() -> None:
    settings = PdfSettings(article_limit_mode="cap_and_continue")

    check(not not settings.article_limit_mode == "cap_and_continue", "assertion failed")


def test_pdf_article_limit_mode_rejects_invalid_value() -> None:
    with pytest.raises(ValidationError, match="article_limit_mode"):
        PdfSettings(article_limit_mode=cast(Any, "typo"))


def test_from_mapping_ignores_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZAMMAD__API_TOKEN", "env-token")

    settings = Settings.from_mapping(
        {
            "zammad": {
                "base_url": "https://zammad.from-mapping.local",
                "api_token": fake_credential("mapping-token"),
            },
            "storage": {"root": "/mnt/archive"},
        }
    )

    check(
        not not str(settings.zammad.base_url).rstrip("/") == "https://zammad.from-mapping.local",
        "assertion failed",
    )
    check(
        not not settings.zammad.api_token.get_secret_value() == "mapping-token", "assertion failed"
    )


def test_storage_path_policy_rejects_stale_sanitize_config() -> None:
    with pytest.raises(ValidationError) as exc:
        Settings.from_mapping(
            {
                "zammad": {
                    "base_url": "https://zammad.example.local",
                    "api_token": fake_credential("test-token"),
                },
                "storage": {
                    "root": "/mnt/archive",
                    "path_policy": {
                        "sanitize": {
                            "replace_whitespace": "_",
                            "strip_control_chars": True,
                        }
                    },
                },
            }
        )

    msg = str(exc.value)
    check(not "storage.path_policy.sanitize" not in msg, "assertion failed")
    check(not "Extra inputs are not permitted" not in msg, "assertion failed")


def test_storage_filename_pattern_rejects_date_utc_alias() -> None:
    with pytest.raises(ValidationError) as exc:
        Settings.from_mapping(
            {
                "zammad": {
                    "base_url": "https://zammad.example.local",
                    "api_token": fake_credential("test-token"),
                },
                "storage": {
                    "root": "/mnt/archive",
                    "path_policy": {
                        "filename_pattern": "Ticket-{ticket_number}_{date_utc}.pdf",
                    },
                },
            }
        )

    msg = str(exc.value)
    check(not "storage.path_policy.filename_pattern" not in msg, "assertion failed")
    check(not "{date_utc}" not in msg, "assertion failed")
    check(not "{timestamp_utc}" not in msg, "assertion failed")


def test_explicit_config_path_missing_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    _clear_env(monkeypatch)

    missing = tmp_path / "missing.yaml"
    with pytest.raises(ConfigValidationError) as exc:
        load_settings(config_path=missing)

    check(not "CONFIG_PATH" not in str(exc.value), "assertion failed")
    check(not "Config file not found" not in str(exc.value), "assertion failed")


def test_yaml_root_must_be_mapping(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    _clear_env(monkeypatch)

    config_path = tmp_path / "config.yaml"
    config_path.write_text("- just\n- a\n- list\n", encoding="utf-8")

    with pytest.raises(ConfigValidationError) as exc:
        load_settings(config_path=config_path)

    check(not "YAML root must be a mapping/object" not in str(exc.value), "assertion failed")
