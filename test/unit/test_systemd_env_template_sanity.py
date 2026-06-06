from __future__ import annotations

from pathlib import Path

from test.support.checks import check
from test.support.env_file_helpers import parse_env_file


def test_systemd_env_template_does_not_force_missing_config_path() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    env_path = repo_root / "infra" / "systemd" / "zammad-archiver.env"
    env = parse_env_file(env_path)

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
