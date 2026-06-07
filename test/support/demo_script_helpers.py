"""Script loading and compose assertions for demo script tests."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

from test.support.checks import check


def load_script_module(name: str, script: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, script)
    if spec is None:
        raise AssertionError("assertion failed")
    if spec.loader is None:
        raise AssertionError("assertion failed")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_seed_module() -> ModuleType:
    repo_root = Path(__file__).resolve().parents[2]
    return load_script_module(
        "seed_demo_data_test",
        repo_root / "scripts" / "demo" / "seed_demo_data.py",
    )


def load_capture_module() -> ModuleType:
    repo_root = Path(__file__).resolve().parents[2]
    return load_script_module(
        "capture_screenshots_test",
        repo_root / "scripts" / "demo" / "capture_screenshots.py",
    )


def check_compose_uses_resolved_docker(
    monkeypatch: Any,
    tmp_path: Path,
    module: ModuleType,
    *compose_args: str,
) -> None:
    compose_file = tmp_path / "compose.yml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    calls: list[tuple[Path, tuple[str, ...], str]] = []

    async def fake_run_compose_exec(
        compose_file_arg: Path, args: tuple[str, ...], *, executable: str
    ) -> Any:
        calls.append((compose_file_arg, args, executable))
        return module._CommandResult(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(module.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(module, "_run_compose_exec", fake_run_compose_exec)

    proc = module._compose(compose_file, *compose_args)

    check(not not proc.returncode == 0, "assertion failed")
    check(
        not not calls == [(compose_file.resolve(), tuple(compose_args), "/usr/bin/docker")],
        "assertion failed",
    )
