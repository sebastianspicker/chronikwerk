from __future__ import annotations

from pathlib import Path

import pytest


def parse_env_file(path: Path, *, skip_on_permission_error: bool = False) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text("utf-8").splitlines()
    except PermissionError:
        if skip_on_permission_error:
            pytest.skip(f"PermissionError reading {path.name} (system locked)")
        raise

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values
