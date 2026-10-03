"""Redact secrets before configuration is logged or returned to operators."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import SecretStr

from chronikwerk.redaction import REDACTED_VALUE, is_sensitive_key, scrub_secrets_in_text


def _redact_value(value: Any) -> Any:
    if isinstance(value, SecretStr):
        return REDACTED_VALUE
    if isinstance(value, str):
        return scrub_secrets_in_text(value)
    if isinstance(value, Mapping):
        return redact_settings_dict(value)
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(item) for item in value)
    return value


def redact_settings_dict(data: Mapping[str, Any]) -> dict[str, Any]:
    """
    Returns a deep-redacted copy of `data` (does not mutate input).

    Redaction rules:
    - Any value under a sensitive key is replaced with `REDACTED_VALUE`.
    - Any `pydantic.SecretStr` value is replaced with `REDACTED_VALUE` even if the key is not known.
    """
    scrubbed: dict[str, Any] = {}
    for key, value in data.items():
        if is_sensitive_key(str(key)):
            scrubbed[str(key)] = REDACTED_VALUE
        else:
            scrubbed[str(key)] = _redact_value(value)
    return scrubbed
