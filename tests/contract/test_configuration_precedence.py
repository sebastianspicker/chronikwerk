"""Pin configuration source precedence and missing-file errors through load_settings."""

from __future__ import annotations

from pathlib import Path

import pytest

from chronikwerk.configuration.load import load_settings
from chronikwerk.configuration.revisions import ManagedConfigStore
from chronikwerk.configuration.validation import ConfigValidationError
from tests.support.settings_factory import write_test_config


def _config(base: Path, *extra: str) -> tuple[Path, ManagedConfigStore]:
    """Write a valid YAML file with extra lines and open its managed-state store."""
    storage = base / "archive"
    storage.mkdir()
    state_dir = base / "state"
    config = base / "config.yaml"
    write_test_config(config, storage, state_dir=state_dir)
    if extra:
        config.write_text(config.read_text(encoding="utf-8") + "\n".join(extra) + "\n")
    return config, ManagedConfigStore(state_dir)


def _stage(store: ManagedConfigStore, tag: str) -> None:
    """Stage a managed overlay that overrides the trigger tag."""
    store.stage(
        {"workflow": {"trigger_tag": tag}},
        expected_revision=store.current_revision(),
        request_id="precedence-test",
    )


def test_yaml_value_overrides_the_default(isolated_config_env: Path) -> None:
    config, _store = _config(isolated_config_env, "workflow:", "  trigger_tag: yaml-tag")

    assert load_settings(config_path=config).workflow.trigger_tag == "yaml-tag"


def test_default_applies_when_no_source_sets_the_value(isolated_config_env: Path) -> None:
    config, _store = _config(isolated_config_env)

    assert load_settings(config_path=config).workflow.trigger_tag == "pdf:sign"


def test_managed_overlay_overrides_yaml(isolated_config_env: Path) -> None:
    config, store = _config(isolated_config_env, "workflow:", "  trigger_tag: yaml-tag")
    _stage(store, "overlay-tag")

    assert load_settings(config_path=config).workflow.trigger_tag == "overlay-tag"


def test_overlay_is_ignored_when_managed_values_are_excluded(isolated_config_env: Path) -> None:
    config, store = _config(isolated_config_env, "workflow:", "  trigger_tag: yaml-tag")
    _stage(store, "overlay-tag")

    settings = load_settings(config_path=config, include_managed=False)

    assert settings.workflow.trigger_tag == "yaml-tag"


def test_environment_variable_overrides_managed_overlay(
    isolated_config_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, store = _config(isolated_config_env, "workflow:", "  trigger_tag: yaml-tag")
    _stage(store, "overlay-tag")
    monkeypatch.setenv("WORKFLOW__TRIGGER_TAG", "env-tag")

    assert load_settings(config_path=config).workflow.trigger_tag == "env-tag"


def test_environment_variable_overrides_yaml_without_overlay(
    isolated_config_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _store = _config(isolated_config_env, "workflow:", "  trigger_tag: yaml-tag")
    monkeypatch.setenv("WORKFLOW__TRIGGER_TAG", "env-tag")

    assert load_settings(config_path=config).workflow.trigger_tag == "env-tag"


def test_canonical_zammad_origin_variable_overrides_yaml(
    isolated_config_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _store = _config(isolated_config_env)
    monkeypatch.setenv("ZAMMAD_ORIGIN", "https://override.example.local")

    settings = load_settings(config_path=config)

    assert str(settings.zammad.base_url).startswith("https://override.example.local")


def test_conflicting_canonical_and_legacy_variables_are_rejected(
    isolated_config_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _store = _config(isolated_config_env)
    monkeypatch.setenv("ZAMMAD_ORIGIN", "https://one.example.local")
    monkeypatch.setenv("ZAMMAD__BASE_URL", "https://two.example.local")

    with pytest.raises(ConfigValidationError, match="conflicts with ZAMMAD__BASE_URL"):
        load_settings(config_path=config)


def test_explicit_missing_config_argument_is_an_error(isolated_config_env: Path) -> None:
    with pytest.raises(ConfigValidationError, match="not found"):
        load_settings(config_path=isolated_config_env / "missing.yaml")


def test_missing_config_path_variable_is_an_error(
    isolated_config_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CONFIG_PATH", str(isolated_config_env / "missing.yaml"))

    with pytest.raises(ConfigValidationError, match="Config file not found"):
        load_settings()


def test_config_path_variable_selects_the_yaml_file(
    isolated_config_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _store = _config(isolated_config_env, "workflow:", "  trigger_tag: from-env-path")
    monkeypatch.setenv("CONFIG_PATH", str(config))

    assert load_settings().workflow.trigger_tag == "from-env-path"


def test_missing_required_sections_are_reported_with_paths(isolated_config_env: Path) -> None:
    with pytest.raises(ConfigValidationError) as error:
        load_settings()

    paths = {issue.path for issue in error.value.issues}
    assert {"zammad.base_url", "zammad.api_token", "storage.root"} <= paths


def test_enabled_metrics_without_token_fails_validation(isolated_config_env: Path) -> None:
    config, _store = _config(isolated_config_env, "observability:", "  metrics_enabled: true")

    with pytest.raises(ConfigValidationError) as error:
        load_settings(config_path=config)

    assert [issue.path for issue in error.value.issues] == ["observability.metrics_bearer_token"]
