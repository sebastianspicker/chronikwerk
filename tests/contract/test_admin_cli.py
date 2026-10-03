"""Pin the chronikwerk-admin command line contract through cli.main."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from chronikwerk.cli import main
from chronikwerk.configuration.revisions import ManagedConfigStore
from tests.support.settings_factory import write_test_config

_SECRET = "test-webhook-hmac-secret-0123456789abcdef"


def _run(monkeypatch: pytest.MonkeyPatch, *args: str) -> int:
    """Invoke the CLI entry point with the given argument vector."""
    monkeypatch.setattr(sys, "argv", ["chronikwerk-admin", *args])
    return main()


def _config(base: Path) -> tuple[Path, ManagedConfigStore]:
    """Write a valid config file and open its managed-state store."""
    storage = base / "archive"
    storage.mkdir()
    state_dir = base / "state"
    config = base / "config.yaml"
    write_test_config(config, storage, state_dir=state_dir)
    return config, ManagedConfigStore(state_dir)


def _stage(store: ManagedConfigStore, overlay: dict[str, object]) -> str:
    """Stage an overlay and return its revision."""
    metadata = store.stage(
        overlay, expected_revision=store.current_revision(), request_id="cli-test"
    )
    return str(metadata["revision"])


def test_validate_config_accepts_a_valid_file(
    isolated_config_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config, _store = _config(isolated_config_env)

    code = _run(monkeypatch, "validate-config", "--config", str(config))

    assert code == 0
    assert capsys.readouterr().out == "✓ Configuration valid\n"


def test_validate_config_reports_a_missing_file(
    isolated_config_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = isolated_config_env / "missing.yaml"

    code = _run(monkeypatch, "validate-config", "--config", str(missing))

    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert captured.err == f"✗ Config not found: {missing}\n"


def test_validate_config_reports_invalid_content(
    isolated_config_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = isolated_config_env / "bad.yaml"
    config.write_text("workflow:\n  require_tag: [unterminated\n", encoding="utf-8")

    code = _run(monkeypatch, "validate-config", "--config", str(config))

    assert code == 1
    assert capsys.readouterr().err.startswith("✗ Configuration invalid:")


def test_dump_config_redacts_secrets(
    isolated_config_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config, _store = _config(isolated_config_env)
    monkeypatch.setenv("CONFIG_PATH", str(config))

    code = _run(monkeypatch, "dump-config")

    output = capsys.readouterr().out
    assert code == 0
    assert "test-token" not in output
    assert _SECRET not in output
    dumped = json.loads(output)
    assert dumped["zammad"]["base_url"] == "https://zammad.example.local/"
    assert dumped["zammad"]["api_token"] != "test-token"
    assert dumped["zammad"]["webhook_hmac_secret"] != _SECRET


def test_dump_config_without_any_configuration_fails(
    isolated_config_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code = _run(monkeypatch, "dump-config")

    assert code == 1
    assert capsys.readouterr().err.startswith("✗ Failed to load configuration:")


def test_no_command_prints_help_and_succeeds(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code = _run(monkeypatch)

    output = capsys.readouterr().out
    assert code == 0
    assert output.startswith("usage: chronikwerk-admin")
    for command in ("validate-config", "dump-config", "list-config-revisions"):
        assert command in output


def test_unknown_command_exits_with_usage_error(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SystemExit) as exit_info:
        _run(monkeypatch, "no-such-command")

    assert exit_info.value.code == 2


def test_list_config_revisions_is_empty_before_any_staging(
    isolated_config_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config, _store = _config(isolated_config_env)

    code = _run(monkeypatch, "list-config-revisions", "--config", str(config))

    assert code == 0
    assert json.loads(capsys.readouterr().out) == []


def test_list_config_revisions_reports_newest_first(
    isolated_config_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config, store = _config(isolated_config_env)
    first = _stage(store, {"workflow": {"trigger_tag": "one"}})
    second = _stage(store, {"workflow": {"trigger_tag": "two"}})

    code = _run(monkeypatch, "list-config-revisions", "--config", str(config))

    listed = json.loads(capsys.readouterr().out)
    assert code == 0
    assert [item["revision"] for item in listed] == [second, first]
    assert listed[0]["changed_paths"] == ["workflow.trigger_tag"]


def test_stage_config_rollback_restores_prior_overlay(
    isolated_config_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config, store = _config(isolated_config_env)
    first = _stage(store, {"workflow": {"trigger_tag": "one"}})
    second = _stage(store, {"workflow": {"trigger_tag": "two"}})

    code = _run(monkeypatch, "stage-config-rollback", first, "--config", str(config))

    staged = json.loads(capsys.readouterr().out)
    assert code == 0
    assert staged["restart_required"] is True
    assert staged["previous_revision"] == second
    assert store.load() == {"workflow": {"trigger_tag": "one"}}


def test_stage_config_rollback_rejects_unknown_revision(
    isolated_config_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config, store = _config(isolated_config_env)
    before = store.current_revision()

    code = _run(monkeypatch, "stage-config-rollback", "not-a-revision", "--config", str(config))

    assert code == 1
    assert capsys.readouterr().err.startswith("✗ Failed to stage configuration rollback:")
    assert store.current_revision() == before
