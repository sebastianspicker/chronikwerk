from __future__ import annotations


def fake_credential(label: str) -> str:
    """Return a deterministic, clearly test-only credential value."""
    return f"test-only-credential--{label}"
