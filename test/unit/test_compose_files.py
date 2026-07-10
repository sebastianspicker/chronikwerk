from __future__ import annotations

from pathlib import Path

import yaml


def _load_compose(path: Path) -> dict:
    return yaml.safe_load(path.read_text("utf-8"))


def test_prod_compose_env_file_is_optional() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    compose = _load_compose(repo_root / "docker-compose.yml")
    service = compose["services"]["zammad-pdf-archiver"]

    env_file = service["env_file"]
    assert env_file == [{"path": "${ARCHIVER_ENV_FILE:-.env}", "required": False}]
    assert service["ports"] == ["${SERVER__PORT:-8080}:${SERVER__PORT:-8080}"]
    environment = service["environment"]
    assert "SERVER__PORT" in environment
    assert "SERVER_PORT" not in environment


def test_dev_compose_env_file_is_optional() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    compose = _load_compose(repo_root / "docker-compose.dev.yml")
    service = compose["services"]["zammad-pdf-archiver"]

    env_file = service["env_file"]
    assert env_file == [{"path": "${ARCHIVER_ENV_FILE:-.env}", "required": False}]
    assert service["ports"] == ["${SERVER__PORT:-8080}:${SERVER__PORT:-8080}"]
    environment = service["environment"]
    assert "OBSERVABILITY__LOG_LEVEL" in environment
    assert "LOG_LEVEL" not in environment


def test_production_image_installs_signing_extra_and_public_config_only() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    dockerfile = (repo_root / "Dockerfile").read_text("utf-8")

    assert 'pip install --no-cache-dir ".[signing]"' in dockerfile
    assert "COPY --chown=app:app config/config.example.yaml" in dockerfile
    assert "COPY --chown=app:app config/ /app/config/" not in dockerfile
