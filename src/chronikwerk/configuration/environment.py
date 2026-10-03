"""Shared process-environment names for loading and ownership checks."""

from __future__ import annotations

import os

CANONICAL_ENV_ALIASES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("ZAMMAD_ORIGIN", "ZAMMAD__BASE_URL", ("zammad", "base_url")),
    ("ZAMMAD_API_TOKEN", "ZAMMAD__API_TOKEN", ("zammad", "api_token")),
    ("ZAMMAD_TIMEOUT_SECONDS", "ZAMMAD__TIMEOUT_SECONDS", ("zammad", "timeout_seconds")),
    (
        "ZAMMAD_ALLOW_PRIVATE_ORIGIN",
        "HARDENING__TRANSPORT__ALLOW_PRIVATE_NETWORKS",
        ("hardening", "transport", "allow_private_networks"),
    ),
    (
        "ZAMMAD_TRUST_ENV",
        "HARDENING__TRANSPORT__TRUST_ENV",
        ("hardening", "transport", "trust_env"),
    ),
)


def environment_owns(path: str) -> bool:
    """Recognize nested and canonical process variables controlling a setting."""
    names = {name.upper() for name in os.environ}
    if path.upper().replace(".", "__") in names:
        return True
    return any(
        canonical in os.environ
        for canonical, _legacy, parts in CANONICAL_ENV_ALIASES
        if ".".join(parts) == path
    )
