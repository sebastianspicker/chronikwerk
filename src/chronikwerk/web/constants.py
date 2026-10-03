"""Shared constants for the web layer."""

DELIVERY_ID_HEADER = "X-Zammad-Delivery"
INGEST_PROTECTED_PATHS: frozenset[str] = frozenset({"/ingest", "/ingest/batch"})


def normalized_delivery_id(value: str | None) -> str | None:
    """Strip a delivery identifier header value and map blank input to ``None``."""
    return (value or "").strip() or None
