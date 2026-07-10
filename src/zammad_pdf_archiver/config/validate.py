"""Project module."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from pydantic import ValidationError

from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.config.transport import validate_url_policy
from zammad_pdf_archiver.domain.errors import PermanentError


@dataclass(frozen=True)
class ConfigValidationIssue:
    """Implement the ConfigValidationIssue operation."""
    path: str
    message: str


class ConfigValidationError(ValueError):
    """Implement the ConfigValidationError operation."""
    def __init__(self, issues: Iterable[ConfigValidationIssue]):
        """Implement the   init   operation."""
        self.issues = list(issues)
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        lines = ["Configuration invalid:"]
        for issue in self.issues:
            lines.append(f"- {issue.path}: {issue.message}")
        return "\n".join(lines)


def issues_from_pydantic_error(error: ValidationError) -> list[ConfigValidationIssue]:
    """Implement the issues from pydantic error operation."""
    issues: list[ConfigValidationIssue] = []
    for item in error.errors(include_url=False):
        loc = ".".join(str(part) for part in item.get("loc", ())) or "<root>"
        msg = item.get("msg", "Invalid value")
        issues.append(ConfigValidationIssue(path=loc, message=msg))
    return issues



def _validate_webhook_auth(settings: Settings, issues: list[ConfigValidationIssue]) -> None:
    secret = settings.zammad.webhook_hmac_secret
    secret_value = secret.get_secret_value().strip() if secret is not None else ""
    if not secret_value:
        issues.append(
            ConfigValidationIssue(
                path="zammad.webhook_hmac_secret",
        message="Missing webhook HMAC secret. Set ZAMMAD__WEBHOOK_HMAC_SECRET.",
            )
        )


def _validate_delivery_id_requirement(
    settings: Settings, issues: list[ConfigValidationIssue]
) -> None:
    if settings.hardening.webhook.require_delivery_id:
        if int(settings.workflow.delivery_id_ttl_seconds) <= 0:
            issues.append(
                ConfigValidationIssue(
                    path="workflow.delivery_id_ttl_seconds",
                    message=(
                        "hardening.webhook.require_delivery_id requires "
                        "workflow.delivery_id_ttl_seconds to be > 0."
                    ),
                )
            )


def _validate_transport(settings: Settings, issues: list[ConfigValidationIssue]) -> None:
    zammad_url = str(settings.zammad.base_url)
    try:
        validate_url_policy(
            zammad_url,
            allow_insecure_http=settings.hardening.transport.allow_insecure_http,
            allow_private_networks=settings.hardening.transport.allow_private_networks,
        )
    except PermanentError as exc:
        issues.append(ConfigValidationIssue(path="zammad.base_url", message=str(exc)))
    if not settings.zammad.verify_tls:
        issues.append(
            ConfigValidationIssue(
                path="zammad.verify_tls",
                message="TLS verification must stay enabled.",
            )
        )
    _validate_tsa_transport(settings, issues=issues)


def _validate_tsa_transport(
    settings: Settings,
    *,
    issues: list[ConfigValidationIssue],
) -> None:
    if not settings.signing.timestamp.enabled:
        return
    tsa_url = settings.signing.timestamp.rfc3161.tsa_url
    if tsa_url is None:
        return
    tsa_url_str = str(tsa_url)
    try:
        validate_url_policy(
            tsa_url_str,
            allow_insecure_http=settings.hardening.transport.allow_insecure_http,
            allow_private_networks=settings.hardening.transport.allow_private_networks,
        )
    except PermanentError as exc:
        issues.append(
            ConfigValidationIssue(
                path="signing.timestamp.rfc3161.tsa_url", message=str(exc)
            )
        )


def _validate_observability(settings: Settings, issues: list[ConfigValidationIssue]) -> None:
    if settings.observability.metrics_enabled:
        token = settings.observability.metrics_bearer_token
        token_value = token.get_secret_value().strip() if token is not None else ""
        if not token_value:
            issues.append(
                ConfigValidationIssue(
                    path="observability.metrics_bearer_token",
                    message="Metrics enabled but observability.metrics_bearer_token is missing.",
                )
            )
    if settings.observability.history_enabled:
        token = settings.observability.history_bearer_token
        if token is None or not token.get_secret_value().strip():
            issues.append(
                ConfigValidationIssue(
                    path="observability.history_bearer_token",
                    message="History enabled but observability.history_bearer_token is missing.",
                )
            )


def _validate_log_level(settings: Settings, issues: list[ConfigValidationIssue]) -> None:
    level = settings.observability.log_level.strip().upper()
    if level not in {"TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        issues.append(
            ConfigValidationIssue(
                path="observability.log_level",
                message="Unsupported log level.",
            )
        )


def validate_settings(settings: Settings) -> None:
    """Implement the validate settings operation."""
    issues: list[ConfigValidationIssue] = []
    _validate_webhook_auth(settings, issues)
    _validate_delivery_id_requirement(settings, issues)
    _validate_transport(settings, issues)
    _validate_observability(settings, issues)
    _validate_log_level(settings, issues)
    if issues:
        raise ConfigValidationError(issues)
