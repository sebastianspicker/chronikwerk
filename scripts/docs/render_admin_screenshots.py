#!/usr/bin/env python3
"""Render deterministic administration previews from the current application source."""

from __future__ import annotations

from render_admin_screenshots_part3 import _captured_at, main
from screenshot_rendering import (
    _authenticated_html,
    _fixed_datetime,
    _inline_assets,
    _render_png,
    _settings,
    render,
)
from source import (
    _BROWSER_RENDER_SCRIPT,
    ACCESS_TOKEN,
    MANIFEST_PATH,
    OUTPUT_DIR,
    REPO_ROOT,
    SCREENSHOTS,
    WEBHOOK_SECRET,
    ScreenshotSpec,
    _base_revision,
    _chromium_version,
    _manifest,
    _playwright_version,
    _sha256,
    _source_hashes,
    _source_paths,
)

__all__ = [
    "ACCESS_TOKEN",
    "MANIFEST_PATH",
    "OUTPUT_DIR",
    "REPO_ROOT",
    "SCREENSHOTS",
    "WEBHOOK_SECRET",
    "ScreenshotSpec",
    "_BROWSER_RENDER_SCRIPT",
    "_authenticated_html",
    "_base_revision",
    "_captured_at",
    "_chromium_version",
    "_fixed_datetime",
    "_inline_assets",
    "_manifest",
    "_playwright_version",
    "_render_png",
    "_settings",
    "_sha256",
    "_source_hashes",
    "_source_paths",
    "main",
    "render",
]


if __name__ == "__main__":
    raise SystemExit(main())
