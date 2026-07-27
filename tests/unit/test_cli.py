"""Verifies CLI commands, safe diagnostics, and rollback validation."""

from __future__ import annotations

import json

from chronikwerk import cli
from chronikwerk.config.managed import ManagedConfigStore


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
    config.write_text(
        "\n".join(
            [
                "zammad:",
                "  base_url: https://zammad.example.local",
                "  api_token: test-token",
                "  webhook_hmac_secret: test-webhook-hmac-secret-0123456789abcdef",
                "storage:",
                f"  root: {tmp_path}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_PATH", str(config))
    monkeypatch.setattr("sys.argv", ["chronikwerk-admin", "dump-config"])

    assert cli.main() == 0
    data = json.loads(capsys.readouterr().out)
    assert data["zammad"]["api_token"] == "[redacted]"


def test_cli_rollback_revalidates_historical_overlay(capsys, monkeypatch, tmp_path) -> None:
    for key in (
        "ZAMMAD__BASE_URL",
        "ZAMMAD__API_TOKEN",
        "ZAMMAD__WEBHOOK_HMAC_SECRET",
        "STORAGE__ROOT",
    ):
        monkeypatch.delenv(key, raising=False)
    state_dir = tmp_path / "admin"
    config = tmp_path / "config.yaml"
    config.write_text(
        "\n".join(
            [
                "zammad:",
                "  base_url: https://zammad.example.local",
                "  api_token: test-token",
                "  webhook_hmac_secret: test-webhook-hmac-secret-0123456789abcdef",
                "storage:",
                f"  root: {tmp_path}",
                "admin:",
                f"  state_dir: {state_dir}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    store = ManagedConfigStore(state_dir)
    invalid = store.stage(
        {"pdf": {"max_articles": -1}},
        expected_revision=store.current_revision(),
        request_id="invalid",
    )
    current = store.stage(
        {"pdf": {"max_articles": 100}},
        expected_revision=invalid["revision"],
        request_id="current",
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "chronikwerk-admin",
            "stage-config-rollback",
            invalid["revision"],
            "--config",
            str(config),
        ],
    )

    assert cli.main() == 1
    assert "Failed to stage configuration rollback" in capsys.readouterr().err
    assert store.current_revision() == current["revision"]
