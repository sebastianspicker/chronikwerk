from __future__ import annotations

from urllib.parse import urlsplit

from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.config.validation_issues import ConfigValidationIssue


def validate_delivery_id_requirement(
    settings: Settings, issues: list[ConfigValidationIssue]
) -> None:
    if settings.hardening.webhook.require_delivery_id:
        if settings.workflow.delivery_id_ttl_seconds <= 0:
            issues.append(
                ConfigValidationIssue(
                    path="workflow.delivery_id_ttl_seconds",
                    message=(
                        "hardening.webhook.require_delivery_id requires "
                        "workflow.delivery_id_ttl_seconds to be > 0."
                    ),
                )
            )


def validate_redis_url(settings: Settings, issues: list[ConfigValidationIssue]) -> None:
    redis_url = settings.workflow.redis_url
    if not redis_url or not redis_url.strip():
        return

    parsed = urlsplit(redis_url.strip())
    if parsed.scheme not in {"redis", "rediss", "unix"}:
        issues.append(
            ConfigValidationIssue(
                path="workflow.redis_url",
                message=(
                    f"Invalid Redis URL scheme {parsed.scheme!r}. "
                    "Expected redis://, rediss://, or unix://."
                ),
            )
        )


def validate_multi_worker_without_redis(
    settings: Settings, issues: list[ConfigValidationIssue]
) -> None:
    """Reject Redis queue execution without Redis-backed idempotency coordination."""
    execution = (settings.workflow.execution_backend or "").strip().lower()
    idempotency = (settings.workflow.idempotency_backend or "").strip().lower()
    if execution == "redis_queue" and idempotency != "redis":
        issues.append(
            ConfigValidationIssue(
                path="workflow.idempotency_backend",
                message=(
                    "execution_backend='redis_queue' requires idempotency_backend='redis' "
                    "to avoid duplicate ticket processing across workers."
                ),
            )
        )
