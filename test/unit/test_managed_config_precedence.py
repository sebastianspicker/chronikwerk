from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from zammad_pdf_archiver.config.load import load_settings
from zammad_pdf_archiver.config.managed import ManagedConfigStore
from zammad_pdf_archiver.config.validate import ConfigValidationError


def _config(tmp_path: Path, state_dir: Path) -> dict[str, object]:
    return {
        "zammad": {
            "base_url": "https://zammad.example.invalid",
            "api_token": "token",
            "webhook_hmac_secret": "webhook-secret",
        },
        "storage": {"root": str(tmp_path / "archive")},
        "hardening": {"transport": {"allow_private_networks": True}},
        "pdf": {"max_articles": 250},
        "admin": {"state_dir": str(state_dir)},
    }


def test_environment_precedes_managed_overlay_and_yaml(tmp_path: Path, monkeypatch) -> None:
    state_dir = tmp_path / "admin"
    store = ManagedConfigStore(state_dir)
    store.stage(
        {"pdf": {"max_articles": 100}},
        expected_revision=store.current_revision(),
        request_id="test",
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(_config(tmp_path, state_dir)), encoding="utf-8")

    monkeypatch.setenv("ZAMMAD__BASE_URL", "https://zammad.example.invalid")
    monkeypatch.setenv("ZAMMAD__API_TOKEN", "token")
    monkeypatch.setenv("ZAMMAD__WEBHOOK_HMAC_SECRET", "webhook-secret")
    monkeypatch.setenv("STORAGE__ROOT", str(tmp_path / "archive"))
    monkeypatch.setenv("PDF__MAX_ARTICLES", "50")
    assert load_settings(config_path=config_path).pdf.max_articles == 50
    monkeypatch.delenv("PDF__MAX_ARTICLES")
    assert load_settings(config_path=config_path).pdf.max_articles == 100
    assert load_settings(config_path=config_path, include_managed=False).pdf.max_articles == 250


def test_enabling_admin_with_short_token_fails_validation(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.yaml"
    data = _config(tmp_path, tmp_path / "admin")
    data["admin"] = {
        "enabled": True,
        "access_token": "too-short",
        "state_dir": str(tmp_path / "admin"),
    }
    config_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    monkeypatch.delenv("ADMIN__ACCESS_TOKEN", raising=False)

    with pytest.raises(ConfigValidationError) as raised:
        load_settings(config_path=config_path)

    assert any(issue.path == "admin.access_token" for issue in raised.value.issues)
