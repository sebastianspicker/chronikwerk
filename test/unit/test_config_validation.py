from __future__ import annotations

import pytest
from pydantic import ValidationError

from test.support.checks import check
from test.support.credentials import fake_credential
from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.config.validate import ConfigValidationError, validate_settings


def test_workflow_redis_backend_requires_redis_url() -> None:
    with pytest.raises(ValidationError) as exc_info:
        Settings.from_mapping(
            {
                "zammad": {"base_url": "https://z.example", "api_token": fake_credential("t")},
                "storage": {"root": "/mnt"},
                "hardening": {
                    "webhook": {
                        "allow_unsigned": True,
                        "allow_unsigned_when_no_secret": bool(1),
                    }
                },
                "workflow": {"idempotency_backend": "redis"},
            }
        )
    check(
        not not (
            "redis_url" in str(exc_info.value).lower() or "redis" in str(exc_info.value).lower()
        ),
        "assertion failed",
    )


def test_workflow_redis_queue_execution_backend_requires_redis_url() -> None:
    with pytest.raises(ValidationError) as exc_info:
        Settings.from_mapping(
            {
                "zammad": {"base_url": "https://z.example", "api_token": fake_credential("t")},
                "storage": {"root": "/mnt"},
                "hardening": {
                    "webhook": {
                        "allow_unsigned": True,
                        "allow_unsigned_when_no_secret": bool(1),
                    }
                },
                "workflow": {"execution_backend": "redis_queue"},
            }
        )
    msg = str(exc_info.value).lower()
    check(not not ("redis_url" in msg or "redis_queue" in msg), "assertion failed")


def test_pdf_attachment_binary_settings_loaded() -> None:
    """PDF attachment binary inclusion settings are accepted and have defaults."""
    settings = Settings.from_mapping(
        {
            "zammad": {"base_url": "https://z.example", "api_token": fake_credential("t")},
            "storage": {"root": "/mnt"},
            "hardening": {
                "webhook": {
                    "allow_unsigned": True,
                    "allow_unsigned_when_no_secret": bool(1),
                }
            },
        }
    )
    check(not settings.pdf.include_attachment_binary is not False, "assertion failed")
    check(
        not not settings.pdf.max_attachment_bytes_per_file == 10 * 1024 * 1024,
        "assertion failed",
    )
    check(not not settings.pdf.max_total_attachment_bytes == 50 * 1024 * 1024, "assertion failed")

    settings2 = Settings.from_mapping(
        {
            "zammad": {"base_url": "https://z.example", "api_token": fake_credential("t")},
            "storage": {"root": "/mnt"},
            "hardening": {
                "webhook": {
                    "allow_unsigned": True,
                    "allow_unsigned_when_no_secret": bool(1),
                }
            },
            "pdf": {
                "include_attachment_binary": True,
                "max_attachment_bytes_per_file": 1024,
                "max_total_attachment_bytes": 4096,
            },
        }
    )
    check(not settings2.pdf.include_attachment_binary is not True, "assertion failed")
    check(not not settings2.pdf.max_attachment_bytes_per_file == 1024, "assertion failed")
    check(not not settings2.pdf.max_total_attachment_bytes == 4096, "assertion failed")


def test_validate_settings_rejects_invalid_log_level() -> None:
    with pytest.raises(ValidationError) as exc:
        Settings.from_mapping(
            {
                "zammad": {
                    "base_url": "https://zammad.example.local",
                    "api_token": fake_credential("test-token"),
                },
                "storage": {"root": "/mnt/archive"},
                "observability": {"log_level": "VERBOSE"},
                "hardening": {
                    "webhook": {
                        "allow_unsigned": True,
                        "allow_unsigned_when_no_secret": bool(1),
                    }
                },
            }
        )

    msg = str(exc.value)
    check(not "observability.log_level" not in msg, "assertion failed")
    check(not "CRITICAL" not in msg, "assertion failed")


def test_validate_settings_requires_webhook_secret_by_default() -> None:
    settings = Settings.from_mapping(
        {
            "zammad": {
                "base_url": "https://zammad.example.local",
                "api_token": fake_credential("test-token"),
            },
            "storage": {"root": "/mnt/archive"},
        }
    )

    with pytest.raises(ConfigValidationError) as exc:
        validate_settings(settings)

    check(not "zammad.webhook_hmac_secret" not in str(exc.value), "assertion failed")


def test_validate_settings_requires_admin_token_when_admin_enabled() -> None:
    settings = Settings.from_mapping(
        {
            "zammad": {
                "base_url": "https://zammad.example.local",
                "api_token": fake_credential("test-token"),
            },
            "storage": {"root": "/mnt/archive"},
            "hardening": {
                "webhook": {
                    "allow_unsigned": True,
                    "allow_unsigned_when_no_secret": bool(1),
                }
            },
            "admin": {"enabled": True},
        }
    )

    with pytest.raises(ConfigValidationError) as exc:
        validate_settings(settings)

    check(not "admin.bearer_token" not in str(exc.value), "assertion failed")


def test_validate_settings_allows_unsigned_webhooks_when_enabled() -> None:
    settings = Settings.from_mapping(
        {
            "zammad": {
                "base_url": "https://zammad.example.local",
                "api_token": fake_credential("test-token"),
            },
            "storage": {"root": "/mnt/archive"},
            "hardening": {
                "webhook": {
                    "allow_unsigned": True,
                    "allow_unsigned_when_no_secret": bool(1),
                }
            },
        }
    )

    validate_settings(settings)


def test_validate_settings_rejects_unsigned_without_no_secret_opt_in() -> None:
    settings = Settings.from_mapping(
        {
            "zammad": {
                "base_url": "https://zammad.example.local",
                "api_token": fake_credential("test-token"),
            },
            "storage": {"root": "/mnt/archive"},
            "hardening": {
                "webhook": {
                    "allow_unsigned": True,
                    "allow_unsigned_when_no_secret": bool(0),
                }
            },
        }
    )

    with pytest.raises(ConfigValidationError) as exc:
        validate_settings(settings)

    check(
        not "hardening.webhook.allow_unsigned_when_no_secret" not in str(exc.value),
        "assertion failed",
    )


def test_validate_settings_rejects_plain_http_upstream_by_default() -> None:
    settings = Settings.from_mapping(
        {
            "zammad": {
                "base_url": "http://zammad.example.local",
                "api_token": fake_credential("test-token"),
            },
            "storage": {"root": "/mnt/archive"},
            "hardening": {
                "webhook": {
                    "allow_unsigned": True,
                    "allow_unsigned_when_no_secret": bool(1),
                }
            },
        }
    )

    with pytest.raises(ConfigValidationError) as exc:
        validate_settings(settings)

    check(not "zammad.base_url" not in str(exc.value), "assertion failed")


def test_validate_settings_rejects_insecure_tls_by_default() -> None:
    settings = Settings.from_mapping(
        {
            "zammad": {
                "base_url": "https://zammad.example.local",
                "api_token": fake_credential("test-token"),
                "verify_tls": False,
            },
            "storage": {"root": "/mnt/archive"},
            "hardening": {
                "webhook": {
                    "allow_unsigned": True,
                    "allow_unsigned_when_no_secret": bool(1),
                }
            },
        }
    )

    with pytest.raises(ConfigValidationError) as exc:
        validate_settings(settings)

    check(not "zammad.verify_tls" not in str(exc.value), "assertion failed")


def test_validate_settings_rejects_loopback_upstream_by_default() -> None:
    settings = Settings.from_mapping(
        {
            "zammad": {"base_url": "https://127.0.0.1", "api_token": fake_credential("test-token")},
            "storage": {"root": "/mnt/archive"},
            "hardening": {
                "webhook": {
                    "allow_unsigned": True,
                    "allow_unsigned_when_no_secret": bool(1),
                }
            },
        }
    )

    with pytest.raises(ConfigValidationError) as exc:
        validate_settings(settings)

    msg = str(exc.value)
    check(not "zammad.base_url" not in msg, "assertion failed")
    check(not "allow_local_upstreams" not in msg, "assertion failed")


def test_validate_settings_allows_loopback_upstream_when_explicitly_enabled() -> None:
    settings = Settings.from_mapping(
        {
            "zammad": {"base_url": "https://127.0.0.1", "api_token": fake_credential("test-token")},
            "storage": {"root": "/mnt/archive"},
            "hardening": {
                "webhook": {"allow_unsigned": bool(1), "allow_unsigned_when_no_secret": bool(1)},
                "transport": {"allow_local_upstreams": True},
            },
        }
    )

    validate_settings(settings)
