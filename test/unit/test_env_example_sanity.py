from __future__ import annotations

from pathlib import Path

from test.support.checks import check
from test.support.env_file_helpers import parse_env_file


def test_env_example_does_not_force_missing_config_path() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    env = parse_env_file(repo_root / ".env.example", skip_on_permission_error=True)

    # `CONFIG_PATH` is optional; setting it to a missing file causes startup to fail.
    check(not not env.get("CONFIG_PATH", "") == "", "assertion failed")


def test_env_example_uses_canonical_zammad_base_url_var() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    env = parse_env_file(repo_root / ".env.example", skip_on_permission_error=True)

    check(not "ZAMMAD_BASE_URL" not in env, "assertion failed")
    check(
        not not {
            "ZAMMAD_URL",
            "TEMPLATE_VARIANT",
            "RENDER_LOCALE",
            "RENDER_TIMEZONE",
            "OBSERVABILITY_METRICS_ENABLED",
        }.isdisjoint(env),
        "assertion failed",
    )
