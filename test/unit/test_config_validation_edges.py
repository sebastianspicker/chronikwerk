from __future__ import annotations

from pathlib import Path

import pytest

from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.config.validate import ConfigValidationError, validate_settings


def _settings(tmp_path: Path, **overrides) -> Settings:
    data = {
        "zammad": {
            "base_url": "https://zammad.example.local",
            "api_token": "test-token",
            "webhook_hmac_secret": "test-secret",
        },
        "storage": {"root": str(tmp_path)},
    }
    data.update(overrides)
    return Settings.from_mapping(data)


def test_metrics_without_token_is_rejected(tmp_path: Path) -> None:
    settings = _settings(tmp_path, observability={"metrics_enabled": True})

    with pytest.raises(ConfigValidationError) as exc:
        validate_settings(settings)

    assert "observability.metrics_bearer_token" in str(exc.value)


def test_delivery_id_requires_ttl(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        workflow={"delivery_id_ttl_seconds": 0},
        hardening={"webhook": {"require_delivery_id": True}},
    )

    with pytest.raises(ConfigValidationError) as exc:
        validate_settings(settings)

    assert "workflow.delivery_id_ttl_seconds" in str(exc.value)


def test_plain_http_tsa_url_is_rejected(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        signing={
            "enabled": False,
            "timestamp": {
                "enabled": True,
                "rfc3161": {"tsa_url": "http://tsa.example.com/rfc3161"},
            },
        },
    )

    with pytest.raises(ConfigValidationError) as exc:
        validate_settings(settings)

    assert "signing.timestamp.rfc3161.tsa_url" in str(exc.value)


def test_localhost_tsa_url_is_rejected(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        signing={
            "enabled": False,
            "timestamp": {
                "enabled": True,
                "rfc3161": {"tsa_url": "https://localhost/rfc3161"},
            },
        },
    )

    with pytest.raises(ConfigValidationError) as exc:
        validate_settings(settings)

    assert "signing.timestamp.rfc3161.tsa_url" in str(exc.value)
