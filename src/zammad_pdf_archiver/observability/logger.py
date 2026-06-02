from __future__ import annotations

import io
import logging
import sys
from typing import Any

import structlog
from structlog.stdlib import ProcessorFormatter

from zammad_pdf_archiver.config.redact import redact_settings_dict, scrub_secrets_in_text

_SENSITIVE_EVENT_KEY_FRAGMENTS = (
    "authorization",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "exception",
    "redis_url",
)


def _scrub_event_dict(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    if not any(
        fragment in str(key).lower()
        for key in event_dict
        for fragment in _SENSITIVE_EVENT_KEY_FRAGMENTS
    ):
        return event_dict
    return redact_settings_dict(event_dict)


def _coerce_log_format(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    return normalized if normalized in {"json", "human"} else None


def _redacted_exception_formatter(sio: Any, exc_info: Any) -> None:
    rendered = io.StringIO()
    structlog.dev.plain_traceback(rendered, exc_info)
    sio.write(scrub_secrets_in_text(rendered.getvalue()))


def configure_logging(
    *,
    log_level: str = "INFO",
    log_format: str | None = None,
) -> None:
    """
    Minimal structlog + stdlib logging configuration.
    """
    resolved_level = log_level.upper()
    resolved_format = _coerce_log_format(log_format) or "human"

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.StackInfoRenderer(),
        _scrub_event_dict,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]

    if resolved_format == "json":
        shared_processors.insert(4, structlog.processors.format_exc_info)

    renderer: Any
    if resolved_format == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(exception_formatter=_redacted_exception_formatter)

    formatter = ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(resolved_level)

    for noisy in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(noisy)
        logger.handlers = []
        logger.propagate = True

    # WeasyPrint triggers verbose fontTools INFO logs during subsetting.
    # Keep app logs operationally useful by default.
    logging.getLogger("fontTools").setLevel(logging.WARNING)

    structlog.configure(
        processors=[
            *shared_processors,
            ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
