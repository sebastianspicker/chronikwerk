from __future__ import annotations

from zammad_pdf_archiver.config.settings import Settings


def secret_bytes(settings: Settings | None) -> bytes | None:
    if settings is None:
        return None

    secret = settings.zammad.webhook_hmac_secret
    if secret is not None:
        value = secret.get_secret_value()
        if value and value.strip():
            return value.encode("utf-8")

    # Backwards-compatible: allow existing shared secret config.
    legacy = settings.server.webhook_shared_secret
    if legacy is not None:
        value = legacy.get_secret_value()
        if value and value.strip():
            return value.encode("utf-8")

    return None
