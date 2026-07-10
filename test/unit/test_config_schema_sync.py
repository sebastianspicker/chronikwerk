from __future__ import annotations

# The validator is imported after the schema fixture is prepared.
# pylint: disable=import-outside-toplevel,wrong-import-order
# ruff: noqa: I001  # Pylint and Ruff classify the in-repository test package differently.

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from test.support.credentials import fake_credential
from zammad_pdf_archiver.config.settings import Settings


def test_config_example_is_valid_yaml() -> None:
    yaml.safe_load(Path("config/config.example.yaml").read_text(encoding="utf-8"))


def _model_paths(model: type[BaseModel], prefix: str = "") -> set[str]:
    paths: set[str] = set()
    for name, field in model.model_fields.items():
        path = f"{prefix}.{name}" if prefix else name
        annotation: Any = field.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            paths.update(_model_paths(annotation, path))
        else:
            paths.add(path)
    return paths


def _yaml_paths(value: Any, prefix: str = "") -> set[str]:
    if not isinstance(value, dict):
        return {prefix}
    paths: set[str] = set()
    for key, child in value.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        paths.update(_yaml_paths(child, path))
    return paths


def _env_key_paths() -> set[str]:
    return {path.upper().replace(".", "__") for path in _model_paths(Settings)}


def test_config_example_constructs_and_validates(tmp_path: Path) -> None:
    data = yaml.safe_load(Path("config/config.example.yaml").read_text(encoding="utf-8"))
    data["storage"]["root"] = str(tmp_path)
    data["zammad"]["base_url"] = "https://zammad.example.local"
    data["zammad"]["api_token"] = fake_credential("api-token")
    data["zammad"]["webhook_hmac_secret"] = fake_credential("webhook")
    settings = Settings.from_mapping(data)
    from zammad_pdf_archiver.config.validate import validate_settings

    validate_settings(settings)


def test_accessible_config_key_inventory_has_no_unknown_keys() -> None:
    known_paths = _model_paths(Settings)
    yaml_data = yaml.safe_load(Path("config/config.example.yaml").read_text(encoding="utf-8"))
    assert _yaml_paths(yaml_data) <= known_paths

    systemd_text = Path("infra/systemd/zammad-archiver.env").read_text(encoding="utf-8")
    systemd_keys = {
        line.split("=", 1)[0].strip()
        for line in systemd_text.splitlines()
        if line.strip() and not line.lstrip().startswith("#") and "=" in line
    }
    assert systemd_keys - {"ARCHIVER_ENV_FILE", "CONFIG_PATH"} <= _env_key_paths()

    compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))
    compose_keys = set(compose["services"]["zammad-pdf-archiver"]["environment"])
    assert compose_keys <= _env_key_paths()

    documented_rows = set(
        re.findall(
            r"\| `([^`]+)` \| [^|]+ \| `([A-Z][A-Z0-9_]+)` \|",
            Path("docs/config-reference.md").read_text(encoding="utf-8"),
        )
    )
    documented_paths = {row[0] for row in documented_rows}
    documented_env = {row[1] for row in documented_rows}
    assert documented_env <= _env_key_paths()
    assert known_paths <= documented_paths
