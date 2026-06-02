from __future__ import annotations

from urllib.parse import unquote

from zammad_pdf_archiver.app.constants import INGEST_PROTECTED_PATHS


def normalized_protected_path(path: object) -> str:
    raw = str(path or "")
    decoded = unquote(raw)
    if decoded != "/" and decoded.endswith("/"):
        decoded = decoded.rstrip("/")
    return decoded


def is_ingest_protected_path(path: object) -> bool:
    return normalized_protected_path(path) in INGEST_PROTECTED_PATHS
