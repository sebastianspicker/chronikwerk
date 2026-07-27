"""Run the service entry point when the package is invoked as a module."""

from __future__ import annotations

from chronikwerk.runtime import main

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
