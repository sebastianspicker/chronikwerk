from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.config.validate import ConfigValidationError, validate_settings


def _settings(tmp_path: Path, **overrides) -> Settings:
    data = {
        "zammad": {
            "base_url": "https://zammad.example.local",
            "api_token": "test-token",
            "webhook_hmac_secret": "test-webhook-hmac-secret-0123456789abcdef",
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


def test_history_without_token_is_rejected(tmp_path: Path) -> None:
    settings = _settings(tmp_path, observability={"history_enabled": True})

    with pytest.raises(ConfigValidationError) as exc:
        validate_settings(settings)

    assert "observability.history_bearer_token" in str(exc.value)


@pytest.mark.parametrize(
    ("path", "overrides"),
    [
        (
            "admin.access_token",
            {
                "admin": {
                    "enabled": True,
                    "access_token": " valid-admin-access-token-0123456789abcdef ",
                }
            },
        ),
        (
            "retry_bearer_token",
            {"retry_bearer_token": " valid-retry-bearer-token-0123456789abcdef "},
        ),
        (
            "observability.metrics_bearer_token",
            {
                "observability": {
                    "metrics_enabled": True,
                    "metrics_bearer_token": " valid-metrics-token-0123456789abcdef ",
                }
            },
        ),
        (
            "observability.history_bearer_token",
            {
                "observability": {
                    "history_enabled": True,
                    "history_bearer_token": " valid-history-token-0123456789abcdef ",
                }
            },
        ),
    ],
)
def test_auth_tokens_reject_surrounding_whitespace(
    tmp_path: Path,
    path: str,
    overrides: dict[str, object],
) -> None:
    settings = _settings(tmp_path, **overrides)

    with pytest.raises(ConfigValidationError) as exc:
        validate_settings(settings)

    assert any(issue.path == path and "whitespace" in issue.message for issue in exc.value.issues)


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
            "enabled": True,
            "pfx_path": str(tmp_path / "signing.pfx"),
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
            "enabled": True,
            "pfx_path": str(tmp_path / "signing.pfx"),
            "timestamp": {
                "enabled": True,
                "rfc3161": {"tsa_url": "https://localhost/rfc3161"},
            },
        },
    )

    with pytest.raises(ConfigValidationError) as exc:
        validate_settings(settings)

    assert "signing.timestamp.rfc3161.tsa_url" in str(exc.value)


def test_timestamp_without_signing_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="Timestamping requires signing.enabled"):
        _settings(
            tmp_path,
            signing={
                "enabled": False,
                "timestamp": {
                    "enabled": True,
                    "rfc3161": {"tsa_url": "https://tsa.example.com/rfc3161"},
                },
            },
        )
