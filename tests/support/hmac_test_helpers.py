"""Shared HMAC construction helpers for focused webhook tests."""

from __future__ import annotations

import hashlib
import hmac


def sign_body(body: bytes, secret: str) -> str:
    """Create the body-only signature accepted by legacy webhook mode."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def sign_strict(delivery_id: str, body: bytes, secret: str) -> str:
    """Create the delivery-bound signature required by strict webhook mode."""
    delivery_id_bytes = delivery_id.strip().encode("utf-8")
    canonical = (
        b"zammad-webhook-v1\0"
        + len(delivery_id_bytes).to_bytes(8, byteorder="big")
        + delivery_id_bytes
        + body
    )
    digest = hmac.new(secret.encode("utf-8"), canonical, hashlib.sha256).hexdigest()
    return f"sha256={digest}"
