from __future__ import annotations

from typing import Any


def parse_signature(
    value: str,
    allowed_algorithms: dict[str, tuple[int, Any]],
) -> tuple[bytes, type, str] | None:
    """Parse X-Hub-Signature (sha1=<hex> or sha256=<hex>)."""
    try:
        algorithm, hex_digest = value.strip().split("=", 1)
    except ValueError:
        return None

    algo_lower = algorithm.strip().lower()
    if algo_lower not in allowed_algorithms:
        return None

    expected_size, digest_ctor = allowed_algorithms[algo_lower]
    hex_digest = hex_digest.strip()
    try:
        digest = bytes.fromhex(hex_digest)
    except ValueError:
        return None

    if len(digest) != expected_size:
        return None

    return (digest, digest_ctor, algo_lower)
