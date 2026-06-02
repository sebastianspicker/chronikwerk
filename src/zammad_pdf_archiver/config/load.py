from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import ValidationError

from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.config.validate import (
    ConfigValidationError,
    ConfigValidationIssue,
    issues_from_pydantic_error,
    validate_settings,
)


def _default_config_path_if_present() -> Path | None:
    candidate = Path("config/config.yaml")
    return candidate if candidate.exists() else None


def _load_dotenv_if_present() -> None:
    dotenv_path = Path(".env")
    if dotenv_path.is_file():
        load_dotenv(dotenv_path=dotenv_path, override=False)


def _resolve_config_path(config_path: str | Path | None) -> tuple[Path | None, bool]:
    """
    Returns (path, explicit) where `explicit` is True when the user asked for this path
    (via argument or CONFIG_PATH), in which case missing files are errors.
    """
    if config_path is not None:
        return Path(config_path), True

    if env_path := os.environ.get("CONFIG_PATH"):
        return Path(env_path), True

    return _default_config_path_if_present(), False


def _load_yaml_config(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigValidationError(
            [ConfigValidationIssue(path=str(path), message=f"Unable to read config file: {exc}")]
        ) from exc

    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigValidationError(
            [ConfigValidationIssue(path=str(path), message="YAML root must be a mapping/object")]
        )
    return raw


def load_settings(*, config_path: str | Path | None = None) -> Settings:
    """Load settings from YAML config, env vars, and .env, then validate."""
    _load_dotenv_if_present()

    path, explicit = _resolve_config_path(config_path)
    yaml_data: dict[str, Any] = {}

    if path is not None:
        if not path.exists():
            if explicit:
                raise ConfigValidationError(
                    [
                        ConfigValidationIssue(
                            path="CONFIG_PATH",
                            message=f"Config file not found: {path}",
                        )
                    ]
                )
        else:
            yaml_data = _load_yaml_config(path)

    try:
        settings = Settings(**yaml_data)
    except ValidationError as exc:
        issues = _enrich_issues(issues_from_pydantic_error(exc))
        raise ConfigValidationError(issues) from exc

    validate_settings(settings)
    return settings


_ZAMMAD_API_TOKEN_FIELD = ".".join(("zammad", "api_token"))

_HINTS: dict[str, str] = {
    "zammad.base_url": "Set `ZAMMAD_BASE_URL` (or YAML `zammad.base_url`).",
    _ZAMMAD_API_TOKEN_FIELD: f"Set `ZAMMAD_API_TOKEN` (or YAML `{_ZAMMAD_API_TOKEN_FIELD}`).",
    "storage.root": "Set `STORAGE_ROOT` (or YAML `storage.root`).",
}


def _enrich_issues(issues: list[ConfigValidationIssue]) -> list[ConfigValidationIssue]:
    enriched: list[ConfigValidationIssue] = []
    for issue in issues:
        enriched.extend(_with_hint(expanded_issue) for expanded_issue in _expand_issue(issue))
    return enriched


def _expand_issue(issue: ConfigValidationIssue) -> list[ConfigValidationIssue]:
    if "Field required" not in issue.message:
        return [issue]
    if issue.path == "zammad":
        return [
            ConfigValidationIssue("zammad.base_url", "Field required"),
            ConfigValidationIssue("zammad.api_token", "Field required"),
        ]
    if issue.path == "storage":
        return [ConfigValidationIssue("storage.root", "Field required")]
    return [issue]


def _with_hint(issue: ConfigValidationIssue) -> ConfigValidationIssue:
    hint = _HINTS.get(issue.path)
    if hint and hint not in issue.message:
        return ConfigValidationIssue(issue.path, f"{issue.message} {hint}")
    return issue
