from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from test.support.checks import check
from test.support.credentials import fake_credential
from test.unit.test_config import _clear_env
from zammad_pdf_archiver.config.load import load_settings
from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.config.validate import ConfigValidationError, validate_settings


def test_load_settings_rejects_signing_enabled_without_pfx_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
                "signing:",
                "  enabled: true",
                "  pades:",
                "    cert_path: /run/secrets/signer.crt",
                "",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigValidationError) as exc:
        load_settings(config_path=config_path)

    check(not "signing.pfx_path is missing" not in str(exc.value), "assertion failed")


def test_signing_pades_rejects_unsupported_key_material() -> None:
    with pytest.raises(ValidationError) as exc:
        Settings.from_mapping(
            {
                "zammad": {
                    "base_url": "https://zammad.example.local",
                    "api_token": fake_credential("test-token"),
                },
                "storage": {"root": "/mnt/archive"},
                "signing": {
                    "pades": {
                        "key_path": "/run/secrets/signer.key",
                        "key_password": fake_credential("secret"),
                    }
                },
            }
        )

    msg = str(exc.value)
    check(not "signing.pades.key_path" not in msg, "assertion failed")
    check(not "signing.pades.key_password" not in msg, "assertion failed")
    check(not "Extra inputs are not permitted" not in msg, "assertion failed")


def test_observability_rejects_stale_json_logs_config() -> None:
    with pytest.raises(ValidationError) as exc:
        Settings.from_mapping(
            {
                "zammad": {
                    "base_url": "https://zammad.example.local",
                    "api_token": fake_credential("test-token"),
                },
                "storage": {"root": "/mnt/archive"},
                "observability": {"json_logs": True},
            }
        )

    msg = str(exc.value)
    check(not "observability.json_logs" not in msg, "assertion failed")
    check(not "Extra inputs are not permitted" not in msg, "assertion failed")


def test_load_settings_accepts_signing_enabled_with_pfx_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
                "signing:",
                "  enabled: true",
                "  pfx_path: /run/secrets/signing.pfx",
                "",
            ]
        ),
        encoding="utf-8",
    )

    settings = load_settings(config_path=config_path)
    check(not settings.signing.enabled is not True, "assertion failed")
    check(not not str(settings.signing.pfx_path) == "/run/secrets/signing.pfx", "assertion failed")


def test_validate_settings_rejects_invalid_redis_url_scheme() -> None:
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
            "workflow": {
                "execution_backend": "redis_queue",
                "redis_url": "http://redis.local:6379",
            },
        }
    )

    with pytest.raises(ConfigValidationError) as exc:
        validate_settings(settings)

    msg = str(exc.value)
    check(not "workflow.redis_url" not in msg, "assertion failed")
    check(not "Invalid Redis URL scheme" not in msg, "assertion failed")


def test_validate_settings_accepts_valid_redis_url() -> None:
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
            "workflow": {
                "execution_backend": "redis_queue",
                "idempotency_backend": "redis",
                "redis_url": "redis://redis.local:6379/0",
            },
        }
    )

    validate_settings(settings)


def test_validate_settings_accepts_rediss_url() -> None:
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
            "workflow": {
                "execution_backend": "redis_queue",
                "idempotency_backend": "redis",
                "redis_url": "rediss://redis.local:6380/0",
            },
        }
    )

    validate_settings(settings)


def test_rate_limit_rps_upper_bound() -> None:
    with pytest.raises(ValidationError):
        Settings.from_mapping(
            {
                "zammad": {"base_url": "https://z.example", "api_token": fake_credential("t")},
                "storage": {"root": "/mnt"},
                "hardening": {
                    "webhook": {
                        "allow_unsigned": bool(1),
                        "allow_unsigned_when_no_secret": bool(1),
                    },
                    "rate_limit": {"rps": 99999},
                },
            }
        )


def test_rate_limit_burst_upper_bound() -> None:
    with pytest.raises(ValidationError):
        Settings.from_mapping(
            {
                "zammad": {"base_url": "https://z.example", "api_token": fake_credential("t")},
                "storage": {"root": "/mnt"},
                "hardening": {
                    "webhook": {
                        "allow_unsigned": bool(1),
                        "allow_unsigned_when_no_secret": bool(1),
                    },
                    "rate_limit": {"burst": 99999},
                },
            }
        )


def test_metrics_enabled_requires_bearer_token() -> None:
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
            "observability": {"metrics_enabled": True},
        }
    )

    with pytest.raises(ConfigValidationError) as exc:
        validate_settings(settings)

    check(not "observability.metrics_bearer_token" not in str(exc.value), "assertion failed")


def test_is_local_upstream_host_localhost_returns_true() -> None:
    from zammad_pdf_archiver.config.validate import _is_local_upstream_host

    check(not _is_local_upstream_host("localhost") is not True, "assertion failed")
    check(not _is_local_upstream_host("localhost.localdomain") is not True, "assertion failed")
    check(not _is_local_upstream_host("127.0.0.1") is not True, "assertion failed")
    check(not _is_local_upstream_host("::1") is not True, "assertion failed")


def test_is_local_upstream_host_external_returns_false() -> None:
    from zammad_pdf_archiver.config.validate import _is_local_upstream_host

    check(not _is_local_upstream_host("example.com") is not False, "assertion failed")
    check(not _is_local_upstream_host("192.168.1.1") is not False, "assertion failed")


def test_require_delivery_id_with_zero_ttl_raises(tmp_path: Path) -> None:
    settings = Settings.from_mapping(
        {
            "zammad": {"base_url": "https://z.example.local", "api_token": fake_credential("t")},
            "storage": {"root": str(tmp_path)},
            "hardening": {
                "webhook": {
                    "allow_unsigned": True,
                    "allow_unsigned_when_no_secret": bool(1),
                    "require_delivery_id": True,
                }
            },
            "workflow": {"delivery_id_ttl_seconds": 0},
        }
    )
    with pytest.raises(ConfigValidationError, match="delivery_id_ttl_seconds"):
        validate_settings(settings)


def test_redis_queue_requires_redis_idempotency_backend(tmp_path: Path) -> None:
    settings = Settings.from_mapping(
        {
            "zammad": {"base_url": "https://z.example.local", "api_token": fake_credential("t")},
            "storage": {"root": str(tmp_path)},
            "hardening": {
                "webhook": {"allow_unsigned": bool(1), "allow_unsigned_when_no_secret": bool(1)}
            },
            "workflow": {
                "execution_backend": "redis_queue",
                "idempotency_backend": "memory",
                "redis_url": "redis://localhost/0",
            },
        }
    )

    with pytest.raises(ConfigValidationError, match="idempotency_backend='redis'"):
        validate_settings(settings)


def test_plain_http_tsa_url_raises_without_allow_insecure(tmp_path: Path) -> None:
    settings = Settings.from_mapping(
        {
            "zammad": {"base_url": "https://z.example.local", "api_token": fake_credential("t")},
            "storage": {"root": str(tmp_path)},
            "hardening": {
                "webhook": {"allow_unsigned": bool(1), "allow_unsigned_when_no_secret": bool(1)},
                "transport": {"allow_insecure_http": False},
            },
            "signing": {
                "enabled": False,
                "timestamp": {
                    "enabled": True,
                    "rfc3161": {"tsa_url": "http://tsa.example.com/rfc3161"},
                },
            },
        }
    )
    with pytest.raises(ConfigValidationError, match="Plain HTTP TSA URL"):
        validate_settings(settings)


def test_localhost_tsa_url_raises_without_allow_local(tmp_path: Path) -> None:
    settings = Settings.from_mapping(
        {
            "zammad": {"base_url": "https://z.example.local", "api_token": fake_credential("t")},
            "storage": {"root": str(tmp_path)},
            "hardening": {
                "webhook": {"allow_unsigned": bool(1), "allow_unsigned_when_no_secret": bool(1)},
                "transport": {"allow_local_upstreams": False, "allow_insecure_http": True},
            },
            "signing": {
                "enabled": False,
                "timestamp": {
                    "enabled": True,
                    "rfc3161": {"tsa_url": "http://localhost/rfc3161"},
                },
            },
        }
    )
    with pytest.raises(ConfigValidationError, match="local.*upstream|tsa_url"):
        validate_settings(settings)


def test_timestamp_enabled_requires_tsa_url(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="tsa_url is missing"):
        Settings.from_mapping(
            {
                "zammad": {
                    "base_url": "https://z.example.local",
                    "api_token": fake_credential("t"),
                },
                "storage": {"root": str(tmp_path)},
                "hardening": {
                    "webhook": {"allow_unsigned": bool(1), "allow_unsigned_when_no_secret": bool(1)}
                },
                "signing": {"timestamp": {"enabled": True}},
            }
        )
