from __future__ import annotations

import pytest
from pydantic import ValidationError

from test.support.checks import check
from test.support.credentials import fake_credential
from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.config.validate import ConfigValidationError, validate_settings


def _allow_unsigned_webhook(*, no_secret: bool = True) -> dict[str, bool]:
    return {
        "allow_unsigned": True,
        "allow_unsigned_when_no_secret": no_secret,
    }


def _settings_mapping(
    *,
    base_url: str = "https://zammad.example.local",
    api_token: str = "test-token",
    storage_root: str = "/mnt/archive",
    hardening: dict[str, object] | None = None,
    **overrides: object,
) -> dict[str, object]:
    values: dict[str, object] = {
        "zammad": {"base_url": base_url, "api_token": fake_credential(api_token)},
        "storage": {"root": storage_root},
    }
    if hardening is not None:
        values["hardening"] = hardening
    values.update(overrides)
    return values


def _settings_with_unsigned_webhook(
    *,
    admin: dict[str, object] | None = None,
    pdf: dict[str, object] | None = None,
) -> Settings:
    values = _settings_mapping(hardening={"webhook": _allow_unsigned_webhook()})
    if admin is not None:
        values["admin"] = admin
    if pdf is not None:
        values["pdf"] = pdf
    return Settings.from_mapping(values)


@pytest.mark.parametrize(
    ("workflow", "expected_fragment"),
    [
        ({"idempotency_backend": "redis"}, "redis"),
        ({"execution_backend": "redis_queue"}, "redis_queue"),
    ],
)
def test_workflow_redis_backends_require_redis_url(
    workflow: dict[str, object],
    expected_fragment: str,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        Settings.from_mapping(
            _settings_mapping(
                base_url="https://z.example",
                api_token="t",
                storage_root="/mnt",
                hardening={"webhook": _allow_unsigned_webhook()},
                workflow=workflow,
            )
        )
    msg = str(exc_info.value).lower()
    check(not not ("redis_url" in msg or expected_fragment in msg), "assertion failed")


def test_pdf_attachment_binary_settings_loaded() -> None:
    """PDF attachment binary inclusion settings are accepted and have defaults."""
    settings = _settings_with_unsigned_webhook()
    check(not settings.pdf.include_attachment_binary is not False, "assertion failed")
    check(
        not not settings.pdf.max_attachment_bytes_per_file == 10 * 1024 * 1024,
        "assertion failed",
    )
    check(not not settings.pdf.max_total_attachment_bytes == 50 * 1024 * 1024, "assertion failed")

    settings2 = _settings_with_unsigned_webhook(
        pdf={
            "include_attachment_binary": True,
            "max_attachment_bytes_per_file": 1024,
            "max_total_attachment_bytes": 4096,
        }
    )
    check(not settings2.pdf.include_attachment_binary is not True, "assertion failed")
    check(not not settings2.pdf.max_attachment_bytes_per_file == 1024, "assertion failed")
    check(not not settings2.pdf.max_total_attachment_bytes == 4096, "assertion failed")


def test_validate_settings_rejects_invalid_log_level() -> None:
    with pytest.raises(ValidationError) as exc:
        Settings.from_mapping(
            _settings_mapping(
                hardening={"webhook": _allow_unsigned_webhook()},
                observability={"log_level": "VERBOSE"},
            )
        )

    msg = str(exc.value)
    check(not "observability.log_level" not in msg, "assertion failed")
    check(not "CRITICAL" not in msg, "assertion failed")


def test_validate_settings_requires_webhook_secret_by_default() -> None:
    settings = Settings.from_mapping(_settings_mapping())

    with pytest.raises(ConfigValidationError) as exc:
        validate_settings(settings)

    check(not "zammad.webhook_hmac_secret" not in str(exc.value), "assertion failed")


def test_validate_settings_requires_admin_token_when_admin_enabled() -> None:
    settings = _settings_with_unsigned_webhook(admin={"enabled": True})

    with pytest.raises(ConfigValidationError) as exc:
        validate_settings(settings)

    check(not "admin.bearer_token" not in str(exc.value), "assertion failed")


def test_validate_settings_allows_unsigned_webhooks_when_enabled() -> None:
    settings = _settings_with_unsigned_webhook()

    validate_settings(settings)


def test_validate_settings_rejects_unsigned_without_no_secret_opt_in() -> None:
    settings = Settings.from_mapping(
        _settings_mapping(hardening={"webhook": _allow_unsigned_webhook(no_secret=False)})
    )

    with pytest.raises(ConfigValidationError) as exc:
        validate_settings(settings)

    check(
        not "hardening.webhook.allow_unsigned_when_no_secret" not in str(exc.value),
        "assertion failed",
    )


def test_validate_settings_rejects_plain_http_upstream_by_default() -> None:
    settings = Settings.from_mapping(
        _settings_mapping(
            base_url="http://zammad.example.local",
            hardening={"webhook": _allow_unsigned_webhook()},
        )
    )

    with pytest.raises(ConfigValidationError) as exc:
        validate_settings(settings)

    check(not "zammad.base_url" not in str(exc.value), "assertion failed")


def test_validate_settings_rejects_insecure_tls_by_default() -> None:
    settings = Settings.from_mapping(
        _settings_mapping(
            zammad={
                "base_url": "https://zammad.example.local",
                "api_token": fake_credential("test-token"),
                "verify_tls": False,
            },
            hardening={"webhook": _allow_unsigned_webhook()},
        )
    )

    with pytest.raises(ConfigValidationError) as exc:
        validate_settings(settings)

    check(not "zammad.verify_tls" not in str(exc.value), "assertion failed")


def test_validate_settings_rejects_loopback_upstream_by_default() -> None:
    settings = Settings.from_mapping(
        _settings_mapping(
            base_url="https://127.0.0.1",
            hardening={"webhook": _allow_unsigned_webhook()},
        )
    )

    with pytest.raises(ConfigValidationError) as exc:
        validate_settings(settings)

    msg = str(exc.value)
    check(not "zammad.base_url" not in msg, "assertion failed")
    check(not "allow_local_upstreams" not in msg, "assertion failed")


def test_validate_settings_allows_loopback_upstream_when_explicitly_enabled() -> None:
    settings = Settings.from_mapping(
        _settings_mapping(
            base_url="https://127.0.0.1",
            hardening={
                "webhook": _allow_unsigned_webhook(),
                "transport": {"allow_local_upstreams": True},
            },
        )
    )

    validate_settings(settings)
