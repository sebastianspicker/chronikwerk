from __future__ import annotations

from pathlib import Path

import yaml


def test_config_example_is_valid_yaml() -> None:
    yaml.safe_load(Path("config/config.example.yaml").read_text(encoding="utf-8"))
