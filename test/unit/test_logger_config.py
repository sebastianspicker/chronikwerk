from __future__ import annotations

import io
import json
import logging
import warnings

import structlog

from test.support.checks import check
from zammad_pdf_archiver.observability.logger import configure_logging


def test_configure_logging_reduces_fonttools_noise() -> None:
    configure_logging(log_level="INFO")
    logger = logging.getLogger("fontTools")
    check(not not logger.getEffectiveLevel() >= logging.WARNING, "assertion failed")


def test_human_logging_does_not_emit_format_exc_info_warning() -> None:
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        configure_logging(log_level="INFO", log_format="human")
        logger = structlog.get_logger("test.logger")
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            logger.exception("expected_exception")

    check(
        not not not any(
            "Remove `format_exc_info` from your processor chain" in str(warning.message)
            for warning in captured
        ),
        "assertion failed",
    )


def _logged_exception_output(*, log_format: str) -> str:
    configure_logging(log_level="INFO", log_format=log_format)
    stream = io.StringIO()
    root = logging.getLogger()
    for handler in root.handlers:
        if isinstance(handler, logging.StreamHandler):
            handler.stream = stream

    logger = structlog.get_logger("test.logger")
    try:
        raise RuntimeError("Authorization: Bearer topsecret token=abc123")
    except RuntimeError:
        logger.exception("expected_exception")

    return stream.getvalue()


def test_human_logging_redacts_secrets_in_exception_traceback() -> None:
    output = _logged_exception_output(log_format="human")
    check(not not "topsecret" not in output, "assertion failed")
    check(not not "abc123" not in output, "assertion failed")


def test_json_logging_redacts_secrets_in_exception_traceback() -> None:
    payload = json.loads(_logged_exception_output(log_format="json"))
    rendered = json.dumps(payload)
    check(not not "topsecret" not in rendered, "assertion failed")
    check(not not "abc123" not in rendered, "assertion failed")
