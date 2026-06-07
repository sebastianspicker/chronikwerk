from __future__ import annotations

import argparse
from pathlib import Path

from test.support.checks import check
from test.support.demo_script_helpers import (
    check_compose_uses_resolved_docker,
    load_capture_module,
    load_seed_module,
)


def test_capture_screenshots_supports_dry_run(capsys) -> None:
    module = load_capture_module()
    args = argparse.Namespace(
        base_url="http://127.0.0.1:18080",
        output_dir=Path("docs/assets/demo"),
        compose_file=Path("docker-compose.demo.yml"),
    )

    rc = module._dry_run(args)
    output = capsys.readouterr().out
    check(not not rc == 0, "assertion failed")
    check(not "01-admin-token-screen.png" not in output, "assertion failed")
    check(not "09-api-503-backend-unavailable.png" not in output, "assertion failed")
    check(
        not "docker compose -f docker-compose.demo.yml stop redis-demo" not in output,
        "assertion failed",
    )


def test_seed_compose_uses_resolved_docker_and_existing_compose_file(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = load_seed_module()
    check_compose_uses_resolved_docker(monkeypatch, tmp_path, module, "stop", "redis-demo")


def test_capture_compose_uses_resolved_docker_and_existing_compose_file(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = load_capture_module()
    check_compose_uses_resolved_docker(monkeypatch, tmp_path, module, "start", "redis-demo")
