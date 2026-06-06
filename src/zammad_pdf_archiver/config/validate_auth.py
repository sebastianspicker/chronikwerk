from __future__ import annotations

from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.config.validation_issues import ConfigValidationIssue


def validate_webhook_auth(settings: Settings, issues: list[ConfigValidationIssue]) -> None:
    # Webhook auth safety: by default, /ingest must be authenticated with a configured secret.
    secret = settings.zammad.webhook_hmac_secret
    legacy = settings.server.webhook_shared_secret
    secret_value = secret.get_secret_value().strip() if secret is not None else ""
    legacy_value = legacy.get_secret_value().strip() if legacy is not None else ""
    if secret_value or legacy_value:
        return

    webhook = settings.hardening.webhook
    if webhook.allow_unsigned and webhook.allow_unsigned_when_no_secret:
        return

    if webhook.allow_unsigned:
        issues.append(
            ConfigValidationIssue(
                path="hardening.webhook.allow_unsigned_when_no_secret",
                message=(
                    "Running without a webhook HMAC secret requires "
                    "hardening.webhook.allow_unsigned_when_no_secret=true."
                ),
            )
        )
        return

    issues.append(
        ConfigValidationIssue(
            path="zammad.webhook_hmac_secret",
            message=(
                "Missing webhook HMAC secret. Set WEBHOOK_HMAC_SECRET "
                "(or set both hardening.webhook.allow_unsigned=true and "
                "hardening.webhook.allow_unsigned_when_no_secret=true for internal/test use)."
            ),
        )
    )


def validate_admin_settings(settings: Settings, issues: list[ConfigValidationIssue]) -> None:
    if settings.admin.enabled:
        token = settings.admin.bearer_token
        token_value = token.get_secret_value().strip() if token is not None else ""
        if not token_value:
            issues.append(
                ConfigValidationIssue(
                    path="admin.bearer_token",
                    message=(
                        "admin.enabled=true requires admin.bearer_token "
                        "(set ADMIN_BEARER_TOKEN)."
                    ),
                )
            )


def validate_metrics_settings(settings: Settings, issues: list[ConfigValidationIssue]) -> None:
    if settings.observability.metrics_enabled:
        token = settings.observability.metrics_bearer_token
        token_value = token.get_secret_value().strip() if token is not None else ""
        if not token_value:
            issues.append(
                ConfigValidationIssue(
                    path="observability.metrics_bearer_token",
                    message=(
                        "observability.metrics_enabled=true requires "
                        "observability.metrics_bearer_token (set METRICS_BEARER_TOKEN)."
                    ),
                )
            )
