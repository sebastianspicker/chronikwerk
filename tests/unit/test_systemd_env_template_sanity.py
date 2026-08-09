"""Verifies systemd environment and Compose wiring use safe required defaults."""

from __future__ import annotations

from pathlib import Path

from tests.support.env_file_helpers import parse_env_file


def test_systemd_env_template_does_not_force_missing_config_path() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    env_path = repo_root / "infra" / "systemd" / "chronikwerk.env.example"
    env = parse_env_file(env_path)

    # The YAML config is optional; default template should not force a missing file.
    assert env.get("CONFIG_PATH", "") == ""


def test_systemd_env_template_marks_webhook_secret_as_required_by_default() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    env_path = repo_root / "infra" / "systemd" / "chronikwerk.env.example"
    text = env_path.read_text("utf-8")
    env = parse_env_file(env_path)

    assert "Webhook authentication is required" in text
    assert "unsigned mode" not in text
    assert env["ZAMMAD__WEBHOOK_HMAC_SECRET"].startswith("CHANGE-ME")


def test_systemd_env_template_uses_nested_settings_keys() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    env_path = repo_root / "infra" / "systemd" / "chronikwerk.env.example"
    env = parse_env_file(env_path)

    assert env["SERVER__HOST"] == "0.0.0.0"
    assert env["SERVER__PORT"] == "8080"
    assert env["OBSERVABILITY__LOG_LEVEL"] == "INFO"
    assert "SERVER_PORT" not in env


def test_systemd_unit_passes_external_env_file_to_compose() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    unit_path = repo_root / "infra" / "systemd" / "chronikwerk.service"
    text = unit_path.read_text("utf-8")

    assert "EnvironmentFile=/etc/chronikwerk/chronikwerk.env" in text
    assert "--env-file ${CHRONIKWERK_ENV_FILE}" in text
