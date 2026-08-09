"""Source and renderer provenance helpers for administration previews."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from chronikwerk._version import __version__

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = REPO_ROOT / "docs" / "screenshots"
MANIFEST_PATH = OUTPUT_DIR / "manifest.json"
ACCESS_TOKEN = "docs-admin-access-token-0123456789abcdef"
WEBHOOK_SECRET = "docs-webhook-hmac-secret-0123456789abcdef"
_BROWSER_RENDER_SCRIPT = r"""
import {readFile} from 'node:fs/promises';
import {chromium} from 'playwright';

const [htmlPath, destination, widthText, heightText] = process.argv.slice(1);
const browser = await chromium.launch({headless: true});
try {
  const page = await browser.newPage({
    viewport: {width: Number(widthText), height: Number(heightText)},
    deviceScaleFactor: 1,
    colorScheme: 'light',
    reducedMotion: 'reduce'
  });
  await page.setContent(await readFile(htmlPath, 'utf8'), {waitUntil: 'load'});
  await page.screenshot({path: destination, fullPage: false, animations: 'disabled'});
} finally {
  await browser.close();
}
"""


@dataclass(frozen=True)
class ScreenshotSpec:
    """Describe one fixed locale, route, and viewport in the public preview set."""

    filename: str
    route: str
    locale: str
    width: int
    height: int


SCREENSHOTS = (
    ScreenshotSpec("admin-overview.png", "/admin", "en-GB", 1440, 1050),
    ScreenshotSpec(
        "admin-configuration.png",
        "/admin/configuration",
        "de-DE",
        1440,
        2400,
    ),
)


_helper_spec = importlib.util.spec_from_file_location(
    "ci_1_helper", REPO_ROOT / "scripts" / "ci" / "ci_1_helper.py"
)
if _helper_spec is None or _helper_spec.loader is None:
    raise ImportError("unable to load ci_1_helper")
_helper_module = importlib.util.module_from_spec(_helper_spec)
_helper_spec.loader.exec_module(_helper_module)
_sha256 = _helper_module.ci_1_helper


def _source_paths() -> list[Path]:
    """List every source input whose changes should invalidate a screenshot claim."""
    explicit = [
        REPO_ROOT / "scripts" / "ci" / "ci_1_helper.py",
        REPO_ROOT / "scripts" / "docs" / "render_admin_screenshots.py",
        REPO_ROOT / "scripts" / "docs" / "render_admin_screenshots_part3.py",
        REPO_ROOT / "scripts" / "docs" / "source.py",
        REPO_ROOT / "scripts" / "docs" / "screenshot_rendering.py",
        REPO_ROOT / "src" / "chronikwerk" / "_version.py",
        REPO_ROOT / "src" / "chronikwerk" / "i18n.py",
        REPO_ROOT / "src" / "chronikwerk" / "app" / "server.py",
        REPO_ROOT / "src" / "chronikwerk" / "config" / "managed.py",
        REPO_ROOT / "src" / "chronikwerk" / "config" / "settings.py",
        REPO_ROOT / "src" / "chronikwerk" / "static" / "admin" / "admin.css",
        REPO_ROOT / "src" / "chronikwerk" / "static" / "admin" / "chronikwerk-mark.svg",
    ]
    admin_modules = sorted((REPO_ROOT / "src" / "chronikwerk" / "app" / "admin").glob("*.py"))
    settings_sources = sorted((REPO_ROOT / "src" / "chronikwerk" / "config").glob("_settings*.py"))
    templates = sorted((REPO_ROOT / "src" / "chronikwerk" / "templates" / "admin").glob("*.html"))
    return sorted({*explicit, *admin_modules, *settings_sources, *templates})


def _source_hashes() -> dict[str, str]:
    """Record source hashes relative to the repository for portable verification."""
    return {path.relative_to(REPO_ROOT).as_posix(): _sha256(path) for path in _source_paths()}


def _base_revision() -> str | None:
    """Capture the current Git revision when available without making it required."""
    result = subprocess.run(  # nosec B603
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() or None if result.returncode == 0 else None


def _chromium_version() -> str:
    """Record the pinned browser engine used for the rendered previews."""
    result = subprocess.run(  # nosec B603
        [
            "node",
            "--input-type=module",
            "-e",
            (
                "import {chromium} from 'playwright';"
                "const browser=await chromium.launch({headless:true});"
                "console.log(browser.version());await browser.close();"
            ),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _playwright_version() -> str:
    """Read the locked Playwright package version without querying a registry."""
    lock = json.loads((REPO_ROOT / "package-lock.json").read_text(encoding="utf-8"))
    return str(lock["packages"]["node_modules/@playwright/test"]["version"])


def _manifest(captured_at: datetime) -> dict[str, object]:
    """Describe source provenance, renderer limits, and exact output checksums."""
    return {
        "schema_version": 2,
        "candidate_version": __version__,
        "base_revision": _base_revision(),
        "source_state": "unfrozen_local_candidate",
        "rendered_at_utc": captured_at.isoformat().replace("+00:00", "Z"),
        "fixture": "synthetic local configuration with empty volatile history",
        "source": (
            "authenticated FastAPI TestClient HTML and shipped admin CSS, rendered in "
            "headless Chromium with JavaScript removed"
        ),
        "source_files": _source_hashes(),
        "renderer": {
            "browser": f"Chromium {_chromium_version()}",
            "playwright": _playwright_version(),
            "javascript_executed": False,
            "browser_verified": False,
        },
        "images": [
            {
                "path": spec.filename,
                "route": spec.route,
                "locale": spec.locale,
                "viewport": {"width": spec.width, "height": spec.height},
                "sha256": _sha256(OUTPUT_DIR / spec.filename),
            }
            for spec in SCREENSHOTS
        ],
    }
