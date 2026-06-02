from __future__ import annotations

from importlib import metadata


def get_package_version(
    dist_name: str,
    fallback: str = "unknown",
    *,
    catch_unexpected: bool = False,
) -> str:
    """Return installed package version, or fallback when configured metadata is unavailable."""
    try:
        return metadata.version(dist_name)
    except metadata.PackageNotFoundError:
        return fallback
    except Exception:
        if catch_unexpected:
            return fallback
        raise
