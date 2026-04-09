"""Shared constants for the app layer."""

DELIVERY_ID_HEADER = "X-Zammad-Delivery"
REQUEST_ID_KEY = "_request_id"
FORCE_REPROCESS_KEY = "_force_reprocess"
INGEST_PROTECTED_PATHS: frozenset[str] = frozenset({"/ingest", "/ingest/batch"})
