"""Shared constants for the web layer."""

from starlette.types import Scope

DELIVERY_ID_HEADER = "X-Zammad-Delivery"
INGEST_PROTECTED_PATHS: frozenset[str] = frozenset({"/ingest", "/ingest/batch"})


def route_path(scope: Scope) -> str:
    """Return the application-relative path using Starlette routing semantics."""
    path = str(scope.get("path") or "")
    root_path = str(scope.get("root_path") or "")
    if not root_path or not path.startswith(root_path):
        return path
    if path == root_path:
        return ""
    if path[len(root_path)] == "/":
        return path[len(root_path) :]
    return path


def normalized_delivery_id(value: str | None) -> str | None:
    """Strip a delivery identifier header value and map blank input to ``None``."""
    return (value or "").strip() or None
