"""Verifies CLI commands, safe diagnostics, and rollback validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from chronikwerk import cli
from chronikwerk.config.managed import ManagedConfigStore
from tests.support.settings_factory import write_test_config


def _clear_config_env(monkeypatch) -> None:
    """Remove ambient runtime values so file-backed CLI tests are deterministic."""
    for key in (
        "ZAMMAD__BASE_URL",
        "ZAMMAD__API_TOKEN",
        "ZAMMAD__WEBHOOK_HMAC_SECRET",
        "STORAGE__ROOT",
    ):
        monkeypatch.delenv(key, raising=False)


def _prepare_rollback_history(
    monkeypatch,
    tmp_path: Path,
    *,
    historical_max_articles: int,
    historical_request_id: str,
) -> tuple[ManagedConfigStore, dict[str, Any], dict[str, Any], Path]:
    """Create historical and current revisions for rollback command tests."""
    _clear_config_env(monkeypatch)
    state_dir = tmp_path / "admin"
    config = tmp_path / "config.yaml"
    write_test_config(config, tmp_path, state_dir=state_dir)
    store = ManagedConfigStore(state_dir)
    historical = store.stage(
        {"pdf": {"max_articles": historical_max_articles}},
        expected_revision=store.current_revision(),
        request_id=historical_request_id,
    )
    current = store.stage(
        {"pdf": {"max_articles": 100}},
        expected_revision=historical["revision"],
        request_id="current",
    )
    return store, historical, current, config


def _select_rollback(monkeypatch, *, revision: str, config: Path) -> None:
    """Select one historical revision through the public CLI argument surface."""
    monkeypatch.setattr(
        "sys.argv",
        [
            "chronikwerk-admin",
            "stage-config-rollback",
            revision,
            "--config",
            str(config),
        ],
    )


def test_main_without_command_prints_help(capsys, monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["chronikwerk-admin"])
    assert cli.main() == 0
    assert "validate-config" in capsys.readouterr().out


def test_validate_config_missing_file_returns_1(capsys, monkeypatch, tmp_path) -> None:
    missing = tmp_path / "missing.yaml"
    monkeypatch.setattr(
        "sys.argv",
        ["chronikwerk-admin", "validate-config", "--config", str(missing)],
    )
    assert cli.main() == 1
    assert "Config not found" in capsys.readouterr().err


def test_dump_config_redacts_secret(capsys, monkeypatch, tmp_path) -> None:
    for key in ("ZAMMAD__BASE_URL", "ZAMMAD__API_TOKEN", "STORAGE__ROOT"):
        monkeypatch.delenv(key, raising=False)
    config = tmp_path / "config.yaml"
    write_test_config(config, tmp_path)
    monkeypatch.setenv("CONFIG_PATH", str(config))
    monkeypatch.setattr("sys.argv", ["chronikwerk-admin", "dump-config"])

    assert cli.main() == 0
    data = json.loads(capsys.readouterr().out)
    assert data["zammad"]["api_token"] == "[redacted]"


def test_validate_config_success(capsys, monkeypatch, tmp_path) -> None:
    _clear_config_env(monkeypatch)
    config = tmp_path / "config.yaml"
    write_test_config(config, tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["chronikwerk-admin", "validate-config", "--config", str(config)],
    )

    assert cli.main() == 0
    assert capsys.readouterr().out == "✓ Configuration valid\n"


def test_validate_config_reports_invalid_content_without_missing_file_message(
    capsys, monkeypatch, tmp_path
) -> None:
    config = tmp_path / "invalid.yaml"
    config.write_text("zammad: []\n", encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        ["chronikwerk-admin", "validate-config", "--config", str(config)],
    )

    assert cli.main() == 1
    assert "Configuration invalid" in capsys.readouterr().err


def test_dump_and_list_commands_report_load_errors(capsys, monkeypatch, tmp_path) -> None:
    missing = tmp_path / "missing.yaml"
    monkeypatch.setenv("CONFIG_PATH", str(missing))
    monkeypatch.setattr("sys.argv", ["chronikwerk-admin", "dump-config"])

    assert cli.main() == 1
    assert "Failed to load configuration" in capsys.readouterr().err

    monkeypatch.setattr(
        "sys.argv",
        ["chronikwerk-admin", "list-config-revisions", "--config", str(missing)],
    )
    assert cli.main() == 1
    assert "Failed to list configuration revisions" in capsys.readouterr().err


def test_list_config_revisions_success(capsys, monkeypatch, tmp_path) -> None:
    _clear_config_env(monkeypatch)
    state_dir = tmp_path / "admin"
    config = tmp_path / "config.yaml"
    write_test_config(config, tmp_path, state_dir=state_dir)
    store = ManagedConfigStore(state_dir)
    staged = store.stage(
        {"pdf": {"max_articles": 50}},
        expected_revision=store.current_revision(),
        request_id="listed",
    )
    monkeypatch.setattr(
        "sys.argv",
        ["chronikwerk-admin", "list-config-revisions", "--config", str(config)],
    )

    assert cli.main() == 0
    revisions = json.loads(capsys.readouterr().out)
    assert revisions[0]["revision"] == staged["revision"]


def test_cli_rollback_stages_historical_overlay(capsys, monkeypatch, tmp_path) -> None:
    store, historical, current, config = _prepare_rollback_history(
        monkeypatch,
        tmp_path,
        historical_max_articles=50,
        historical_request_id="historical",
    )
    _select_rollback(
        monkeypatch,
        revision=historical["revision"],
        config=config,
    )

    assert cli.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["restart_required"] is True
    assert result["revision"] not in {historical["revision"], current["revision"]}
    assert store.revision_overlay(result["revision"])["pdf"]["max_articles"] == 50


def test_cli_rollback_revalidates_historical_overlay(capsys, monkeypatch, tmp_path) -> None:
    store, invalid, current, config = _prepare_rollback_history(
        monkeypatch,
        tmp_path,
        historical_max_articles=-1,
        historical_request_id="invalid",
    )
    _select_rollback(
        monkeypatch,
        revision=invalid["revision"],
        config=config,
    )

    assert cli.main() == 1
    assert "Failed to stage configuration rollback" in capsys.readouterr().err
    assert store.current_revision() == current["revision"]
