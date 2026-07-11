from __future__ import annotations

from pathlib import Path


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text("utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def test_systemd_env_template_does_not_force_missing_config_path() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    env_path = repo_root / "infra" / "systemd" / "zammad-archiver.env"
    env = _parse_env_file(env_path)

    # The YAML config is optional; default template should not force a missing file.
    assert env.get("CONFIG_PATH", "") == ""


def test_systemd_env_template_marks_webhook_secret_as_required_by_default() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    env_path = repo_root / "infra" / "systemd" / "zammad-archiver.env"
    text = env_path.read_text("utf-8")

    assert "Webhook auth (required unless you explicitly enable unsigned mode" in text
    assert "ZAMMAD__WEBHOOK_HMAC_SECRET" in text


def test_systemd_env_template_uses_nested_settings_keys() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    env_path = repo_root / "infra" / "systemd" / "zammad-archiver.env"
    env = _parse_env_file(env_path)

    assert env["SERVER__HOST"] == "0.0.0.0"
    assert env["SERVER__PORT"] == "8080"
    assert env["OBSERVABILITY__LOG_LEVEL"] == "INFO"
    assert "SERVER_PORT" not in env


def test_systemd_unit_passes_external_env_file_to_compose() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    unit_path = repo_root / "infra" / "systemd" / "zammad-archiver.service"
    text = unit_path.read_text("utf-8")

    assert "EnvironmentFile=/etc/zammad-archiver/zammad-archiver.env" in text
    assert "--env-file ${ARCHIVER_ENV_FILE}" in text
