from __future__ import annotations

from pathlib import Path

from test.support.checks import check


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
    check(not not env.get("CONFIG_PATH", "") == "", "assertion failed")


def test_systemd_env_template_marks_webhook_secret_as_required_by_default() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    env_path = repo_root / "infra" / "systemd" / "zammad-archiver.env"
    text = env_path.read_text("utf-8")

    check(
        not "Webhook auth (required unless you explicitly enable unsigned mode" not in text,
        "assertion failed",
    )
    check(not "WEBHOOK_HMAC_SECRET" not in text, "assertion failed")
