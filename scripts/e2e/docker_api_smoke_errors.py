from __future__ import annotations

PROCESSED_STATUS = "processed"


class E2EFailure(RuntimeError):
    """Raised when the Docker API E2E lane cannot prove the expected behavior."""
