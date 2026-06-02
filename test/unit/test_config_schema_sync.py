from __future__ import annotations

import json
from pathlib import Path
from typing import get_args, get_origin

import yaml
from pydantic import BaseModel

from test.support.checks import check
from zammad_pdf_archiver.config.env_aliases import _CANONICAL_MAPPINGS
from zammad_pdf_archiver.config.settings import Settings

_PUBLIC_SETTINGS_WITHOUT_FLAT_ENV_ALIAS: dict[str, str] = {
    "workflow.acknowledge_on_success": "YAML and nested env only; no flat alias is documented.",
    "storage.path_policy.allow_prefixes": "YAML and nested env only for list-valued policy.",
    "storage.path_policy.filename_pattern": (
        "YAML and nested env only; filename aliases are under investigation."
    ),
    "hardening.webhook.webhook_reject_sha1": (
        "YAML and nested env only; no flat alias is documented."
    ),
}


def _load_schema(repo_root: Path) -> dict:
    return json.loads((repo_root / "config" / "config.schema.json").read_text(encoding="utf-8"))


def _load_example(repo_root: Path) -> dict:
    raw = yaml.safe_load((repo_root / "config" / "config.example.yaml").read_text(encoding="utf-8"))
    check(not not isinstance(raw, dict), "assertion failed")
    return raw


def _nested_model_type(annotation: object) -> type[BaseModel] | None:
    candidates = get_args(annotation) if get_origin(annotation) is not None else (annotation,)
    for candidate in candidates:
        if isinstance(candidate, type) and issubclass(candidate, BaseModel):
            return candidate
    return None


def _settings_key_paths(model: type[BaseModel], prefix: tuple[str, ...] = ()) -> set[str]:
    paths: set[str] = set()
    for name, field in model.model_fields.items():
        nested_model = _nested_model_type(field.annotation)
        current = (*prefix, name)
        if nested_model is not None:
            paths.update(_settings_key_paths(nested_model, current))
        else:
            paths.add(".".join(current))
    return paths


def _schema_key_paths(node: dict, prefix: tuple[str, ...] = ()) -> set[str]:
    paths: set[str] = set()
    for name, child in node.get("properties", {}).items():
        current = (*prefix, name)
        if isinstance(child, dict) and isinstance(child.get("properties"), dict):
            paths.update(_schema_key_paths(child, current))
        else:
            paths.add(".".join(current))
    return paths


def _example_key_paths(node: dict, prefix: tuple[str, ...] = ()) -> set[str]:
    paths: set[str] = set()
    for name, value in node.items():
        current = (*prefix, str(name))
        if isinstance(value, dict):
            paths.update(_example_key_paths(value, current))
        else:
            paths.add(".".join(current))
    return paths


def test_config_schema_keys_match_settings_model() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    settings_keys = _settings_key_paths(Settings)
    schema_keys = _schema_key_paths(_load_schema(repo_root))

    check(not not schema_keys == settings_keys, "assertion failed")


def test_config_example_keys_match_settings_model_with_explicit_omissions() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    settings_keys = _settings_key_paths(Settings)
    example_keys = _example_key_paths(_load_example(repo_root))

    missing = settings_keys - example_keys
    extra = example_keys - settings_keys

    check(not not missing == set(), "assertion failed")
    check(not not extra == set(), "assertion failed")


def test_flat_env_aliases_reference_current_settings_keys() -> None:
    settings_keys = _settings_key_paths(Settings)
    canonical_paths = {".".join(path) for _env_name, path in _CANONICAL_MAPPINGS}

    check(not not canonical_paths <= settings_keys, "assertion failed")
    check(
        not not settings_keys - canonical_paths == set(_PUBLIC_SETTINGS_WITHOUT_FLAT_ENV_ALIAS),
        "assertion failed",
    )


def test_config_schema_includes_runtime_settings_extensions() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    schema = _load_schema(repo_root)
    props = schema["properties"]

    workflow_props = props["workflow"]["properties"]
    check(not "execution_backend" not in workflow_props, "assertion failed")
    check(not "idempotency_backend" not in workflow_props, "assertion failed")
    check(not "redis_url" not in workflow_props, "assertion failed")
    check(not "queue_stream" not in workflow_props, "assertion failed")
    check(not "queue_group" not in workflow_props, "assertion failed")
    check(not "queue_read_block_ms" not in workflow_props, "assertion failed")
    check(not "queue_read_count" not in workflow_props, "assertion failed")
    check(not "queue_retry_max_attempts" not in workflow_props, "assertion failed")
    check(not "queue_retry_backoff_seconds" not in workflow_props, "assertion failed")
    check(not "queue_dlq_stream" not in workflow_props, "assertion failed")
    check(not "history_stream" not in workflow_props, "assertion failed")
    check(not "history_retention_maxlen" not in workflow_props, "assertion failed")

    fields_props = props["fields"]["properties"]
    check(not "archive_user" not in fields_props, "assertion failed")

    storage_props = props["storage"]["properties"]
    check(not "fsync" not in storage_props, "assertion failed")
    check(not not "atomic_write" not in storage_props, "assertion failed")

    pdf_props = props["pdf"]["properties"]
    check(not "article_limit_mode" not in pdf_props, "assertion failed")
    check(not "include_attachment_binary" not in pdf_props, "assertion failed")
    check(not "max_attachment_bytes_per_file" not in pdf_props, "assertion failed")
    check(not "max_total_attachment_bytes" not in pdf_props, "assertion failed")

    obs_props = props["observability"]["properties"]
    check(not "metrics_bearer_token" not in obs_props, "assertion failed")
    check(not "healthz_omit_version" not in obs_props, "assertion failed")

    webhook_props = props["hardening"]["properties"]["webhook"]["properties"]
    check(not "allow_unsigned_when_no_secret" not in webhook_props, "assertion failed")

    rate_limit_props = props["hardening"]["properties"]["rate_limit"]["properties"]
    check(not "client_key_header" not in rate_limit_props, "assertion failed")

    transport_props = props["hardening"]["properties"]["transport"]["properties"]
    check(not "allow_local_upstreams" not in transport_props, "assertion failed")

    admin_props = props["admin"]["properties"]
    check(not "enabled" not in admin_props, "assertion failed")
    check(not "bearer_token" not in admin_props, "assertion failed")
    check(not "history_limit" not in admin_props, "assertion failed")


def test_config_example_contains_supported_keys() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    config = _load_example(repo_root)

    check(
        not not ("workflow" in config and isinstance(config["workflow"], dict)), "assertion failed"
    )
    check(not "execution_backend" not in config["workflow"], "assertion failed")
    check(not "idempotency_backend" not in config["workflow"], "assertion failed")
    check(not "redis_url" not in config["workflow"], "assertion failed")
    check(not "queue_stream" not in config["workflow"], "assertion failed")
    check(not "queue_group" not in config["workflow"], "assertion failed")
    check(not "queue_read_block_ms" not in config["workflow"], "assertion failed")
    check(not "queue_read_count" not in config["workflow"], "assertion failed")
    check(not "queue_retry_max_attempts" not in config["workflow"], "assertion failed")
    check(not "queue_retry_backoff_seconds" not in config["workflow"], "assertion failed")
    check(not "queue_dlq_stream" not in config["workflow"], "assertion failed")
    check(not "history_stream" not in config["workflow"], "assertion failed")
    check(not "history_retention_maxlen" not in config["workflow"], "assertion failed")

    check(not not ("fields" in config and isinstance(config["fields"], dict)), "assertion failed")
    check(not "archive_user" not in config["fields"], "assertion failed")

    check(not not ("storage" in config and isinstance(config["storage"], dict)), "assertion failed")
    check(not "fsync" not in config["storage"], "assertion failed")
    check(not not "atomic_write" not in config["storage"], "assertion failed")

    check(not not ("pdf" in config and isinstance(config["pdf"], dict)), "assertion failed")
    check(not "article_limit_mode" not in config["pdf"], "assertion failed")
    check(not "include_attachment_binary" not in config["pdf"], "assertion failed")
    check(not "max_attachment_bytes_per_file" not in config["pdf"], "assertion failed")
    check(not "max_total_attachment_bytes" not in config["pdf"], "assertion failed")

    check(
        not not ("observability" in config and isinstance(config["observability"], dict)),
        "assertion failed",
    )
    check(not "metrics_bearer_token" not in config["observability"], "assertion failed")
    check(not "healthz_omit_version" not in config["observability"], "assertion failed")

    check(
        not not ("hardening" in config and isinstance(config["hardening"], dict)),
        "assertion failed",
    )
    check(
        not "allow_unsigned_when_no_secret" not in config["hardening"]["webhook"],
        "assertion failed",
    )
    check(not "client_key_header" not in config["hardening"]["rate_limit"], "assertion failed")
    check(not "allow_local_upstreams" not in config["hardening"]["transport"], "assertion failed")

    check(not not ("admin" in config and isinstance(config["admin"], dict)), "assertion failed")
    check(not "enabled" not in config["admin"], "assertion failed")
    check(not "bearer_token" not in config["admin"], "assertion failed")
    check(not "history_limit" not in config["admin"], "assertion failed")
