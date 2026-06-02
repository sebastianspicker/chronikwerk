from __future__ import annotations

import importlib.util
import json
import struct
from pathlib import Path
from types import ModuleType

from test.support.checks import check

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_manifest(repo_root: Path) -> list[str]:
    manifest_path = repo_root / "docs" / "assets" / "demo" / "screenshot-manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    check(not not isinstance(payload, list), "assertion failed")
    check(not not all(isinstance(item, str) for item in payload), "assertion failed")
    return payload


def _png_dimensions(path: Path) -> tuple[int, int]:
    header = path.read_bytes()[:24]
    check(not not header.startswith(PNG_SIGNATURE), f"{path.name} is not a PNG")
    check(not not header[12:16] == b"IHDR", f"{path.name} has no IHDR chunk")
    width, height = struct.unpack(">II", header[16:24])
    return width, height


def _load_capture_module(repo_root: Path) -> ModuleType:
    script = repo_root / "scripts" / "demo" / "capture_screenshots.py"
    spec = importlib.util.spec_from_file_location("capture_screenshots_manifest_test", script)
    if spec is None or spec.loader is None:
        raise AssertionError("capture_screenshots.py could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_demo_screenshot_manifest_matches_capture_plan() -> None:
    repo_root = _repo_root()
    payload = _load_manifest(repo_root)
    expected = [
        "01-admin-token-screen.png",
        "02-admin-queue-stats.png",
        "03-admin-history-all.png",
        "04-admin-history-filtered-ticket.png",
        "05-admin-retry-action.png",
        "06-admin-dlq-before-drain.png",
        "07-admin-dlq-after-drain.png",
        "08-api-401-unauthorized.png",
        "09-api-503-backend-unavailable.png",
        "10-admin-mobile-viewport.png",
    ]
    check(not not payload == expected, "assertion failed")
    capture_module = _load_capture_module(repo_root)
    check(not not payload == list(capture_module.SHOT_FILENAMES), "assertion failed")


def test_demo_screenshot_manifest_references_valid_png_assets() -> None:
    repo_root = _repo_root()
    payload = _load_manifest(repo_root)
    asset_dir = repo_root / "docs" / "assets" / "demo"

    check(not not len(payload) == len(set(payload)), "manifest contains duplicate screenshot names")
    check(
        not not sorted(path.name for path in asset_dir.glob("*.png")) == payload,
        "assertion failed",
    )

    for name in payload:
        check(not not "/" not in name, "assertion failed")
        check(not not name.endswith(".png"), "assertion failed")
        path = asset_dir / name
        check(not not path.is_file(), f"manifest references missing screenshot {name}")
        check(not not path.stat().st_size > len(PNG_SIGNATURE), f"{name} is empty")
        width, height = _png_dimensions(path)
        check(not not width > 0, "assertion failed")
        check(not not height > 0, "assertion failed")
