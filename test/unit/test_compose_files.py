from __future__ import annotations

from pathlib import Path

import yaml

from test.support.checks import check


def _load_compose(path: Path) -> dict:
    return yaml.safe_load(path.read_text("utf-8"))


def test_prod_compose_env_file_is_optional() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    compose = _load_compose(repo_root / "docker-compose.yml")
    service = compose["services"]["zammad-pdf-archiver"]

    env_file = service["env_file"]
    check(
        not not env_file == [{"path": "${ARCHIVER_ENV_FILE:-.env}", "required": False}],
        "assertion failed",
    )


def test_dev_compose_env_file_is_optional() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    compose = _load_compose(repo_root / "docker-compose.dev.yml")
    service = compose["services"]["zammad-pdf-archiver"]

    env_file = service["env_file"]
    check(not not env_file == [{"path": ".env", "required": False}], "assertion failed")
