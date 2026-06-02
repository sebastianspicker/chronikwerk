"""NFR6: Configuration via env and optional YAML; validate_settings at load."""

from __future__ import annotations

from pathlib import Path

import pytest

from test.support.checks import check
from zammad_pdf_archiver.config.load import load_settings
from zammad_pdf_archiver.config.validate import ConfigValidationError


def test_nfr6_load_settings_from_yaml(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """NFR6: load_settings must load from YAML when config_path points to valid file."""
    monkeypatch.chdir(tmp_path)
    for key in ("CONFIG_PATH", "ZAMMAD_BASE_URL", "ZAMMAD_API_TOKEN", "STORAGE_ROOT"):
        monkeypatch.delenv(key, raising=False)

    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(
        "zammad:\n  base_url: https://zammad.example.local\n  api_token: test-token\n"
        "storage:\n  root: /mnt/archive\n"
        "hardening:\n"
        "  webhook:\n"
        "    allow_unsigned: true\n"
        "    allow_unsigned_when_no_secret: true\n",
        encoding="utf-8",
    )
    settings = load_settings(config_path=yaml_path)
    check(
        not not str(settings.zammad.base_url).rstrip("/") == "https://zammad.example.local",
        "assertion failed",
    )
    check(not not settings.storage.root.as_posix() == "/mnt/archive", "assertion failed")


def test_nfr6_validate_settings_called_on_load(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """NFR6: load_settings must run validate_settings (invalid config raises)."""
    monkeypatch.chdir(tmp_path)
    for key in ("CONFIG_PATH", "ZAMMAD_BASE_URL", "ZAMMAD_API_TOKEN", "STORAGE_ROOT", "LOG_LEVEL"):
        monkeypatch.delenv(key, raising=False)

    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(
        "zammad:\n  base_url: https://zammad.example.local\n  api_token: test-token\n"
        "storage:\n  root: /mnt/archive\n"
        "observability:\n  log_level: INVALID_LEVEL\n"
        "hardening:\n"
        "  webhook:\n"
        "    allow_unsigned: true\n"
        "    allow_unsigned_when_no_secret: true\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigValidationError) as exc_info:
        load_settings(config_path=yaml_path)
    check(
        not not (
            "log_level" in str(exc_info.value).lower() or "invalid" in str(exc_info.value).lower()
        ),
        "assertion failed",
    )
