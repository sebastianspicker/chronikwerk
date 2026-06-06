from __future__ import annotations

from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.config.validate_auth import (
    validate_admin_settings as _validate_admin_settings,
)
from zammad_pdf_archiver.config.validate_auth import (
    validate_metrics_settings as _validate_metrics_settings,
)
from zammad_pdf_archiver.config.validate_auth import (
    validate_webhook_auth as _validate_webhook_auth,
)
from zammad_pdf_archiver.config.validate_transport import (
    is_local_upstream_host as _is_local_upstream_host,
)
from zammad_pdf_archiver.config.validate_transport import (
    validate_primary_transport as _validate_primary_transport,
)
from zammad_pdf_archiver.config.validate_transport import (
    validate_tsa_transport as _validate_tsa_transport,
)
from zammad_pdf_archiver.config.validate_workflow import (
    validate_delivery_id_requirement as _validate_delivery_id_requirement,
)
from zammad_pdf_archiver.config.validate_workflow import (
    validate_multi_worker_without_redis as _validate_multi_worker_without_redis,
)
from zammad_pdf_archiver.config.validate_workflow import (
    validate_redis_url as _validate_redis_url,
)
from zammad_pdf_archiver.config.validation_issues import (
    ConfigValidationError,
    ConfigValidationIssue,
    issues_from_pydantic_error,
)

__all__ = [
    "ConfigValidationError",
    "ConfigValidationIssue",
    "_is_local_upstream_host",
    "issues_from_pydantic_error",
    "validate_settings",
]


def validate_settings(settings: Settings) -> None:
    """Run all cross-field validation rules; raises ConfigValidationError on failure."""
    issues: list[ConfigValidationIssue] = []
    transport = settings.hardening.transport

    _validate_primary_transport(settings, transport=transport, issues=issues)
    _validate_webhook_auth(settings, issues)
    _validate_delivery_id_requirement(settings, issues)
    _validate_tsa_transport(settings, transport=transport, issues=issues)
    _validate_redis_url(settings, issues)
    _validate_admin_settings(settings, issues)
    _validate_metrics_settings(settings, issues)
    _validate_multi_worker_without_redis(settings, issues)

    if issues:
        raise ConfigValidationError(issues)
