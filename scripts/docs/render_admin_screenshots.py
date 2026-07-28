#!/usr/bin/env python3
"""Render deterministic administration previews from the current application source."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from chronikwerk._version import __version__
from chronikwerk.app.admin import _page_routes
from chronikwerk.app.server import create_app
from chronikwerk.config.settings import Settings

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


def _sha256(path: Path) -> str:
    """Hash a binary input in chunks so large assets do not inflate memory use."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_paths() -> list[Path]:
    """List every source input whose changes should invalidate a screenshot claim."""
    explicit = [
        REPO_ROOT / "scripts" / "docs" / "render_admin_screenshots.py",
        REPO_ROOT / "src" / "chronikwerk" / "_version.py",
        REPO_ROOT / "src" / "chronikwerk" / "i18n.py",
        REPO_ROOT / "src" / "chronikwerk" / "app" / "server.py",
        REPO_ROOT / "src" / "chronikwerk" / "config" / "managed.py",
        REPO_ROOT / "src" / "chronikwerk" / "config" / "settings.py",
        REPO_ROOT / "src" / "chronikwerk" / "static" / "admin" / "admin.css",
        REPO_ROOT / "src" / "chronikwerk" / "static" / "admin" / "chronikwerk-mark.svg",
    ]
    admin_modules = sorted((REPO_ROOT / "src" / "chronikwerk" / "app" / "admin").glob("*.py"))
    templates = sorted((REPO_ROOT / "src" / "chronikwerk" / "templates" / "admin").glob("*.html"))
    return sorted({*explicit, *admin_modules, *templates})


def _source_hashes() -> dict[str, str]:
    """Record source hashes relative to the repository for portable verification."""
    return {path.relative_to(REPO_ROOT).as_posix(): _sha256(path) for path in _source_paths()}


def _captured_at(value: str | None) -> datetime:
    """Parse a timezone-aware timestamp, or choose a second-stable UTC capture time."""
    if value is None:
        return datetime.now(UTC).replace(microsecond=0)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("--captured-at must include a timezone")
    return parsed.astimezone(UTC).replace(microsecond=0)


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


def _settings(work_dir: Path) -> Settings:
    """Build isolated synthetic settings so captures never use operator configuration."""
    archive_root = work_dir / "archive"
    state_dir = work_dir / "admin-state"
    archive_root.mkdir(mode=0o700)
    state_dir.mkdir(mode=0o700)
    return Settings.from_mapping(
        {
            "zammad": {
                "base_url": "https://zammad.example.local",
                "api_token": "synthetic-docs-api-token",
                "webhook_hmac_secret": WEBHOOK_SECRET,
            },
            "storage": {"root": archive_root},
            "admin": {
                "enabled": True,
                "access_token": ACCESS_TOKEN,
                "state_dir": state_dir,
                "cookie_secure": False,
            },
        }
    )


def _fixed_datetime(captured_at: datetime) -> type[datetime]:
    """Provide the page route module a clock fixed to the declared capture instant."""

    class FixedDatetime(datetime):
        """Datetime replacement that keeps generated status timestamps reproducible."""

        @classmethod
        def now(cls, tz=None):  # noqa: ANN001
            """Return the capture time in the timezone requested by the page renderer."""
            if tz is None:
                return captured_at.replace(tzinfo=None)
            return captured_at.astimezone(tz)

    return FixedDatetime


def _authenticated_html(
    client: TestClient,
    *,
    spec: ScreenshotSpec,
) -> str:
    """Create a real admin session and fetch the rendered page used in each image."""
    login = client.post(
        "/admin/api/v1/session",
        json={"access_token": ACCESS_TOKEN, "locale": spec.locale},
    )
    if login.status_code != 204:
        raise RuntimeError(f"admin login failed with HTTP {login.status_code}")
    response = client.get(spec.route)
    if response.status_code != 200:
        raise RuntimeError(f"{spec.route} failed with HTTP {response.status_code}")
    return response.text


def _inline_assets(html: str, *, spec: ScreenshotSpec) -> str:
    """Embed shipped visual assets and remove JavaScript for a deterministic render."""
    css_path = REPO_ROOT / "src" / "chronikwerk" / "static" / "admin" / "admin.css"
    mark_path = REPO_ROOT / "src" / "chronikwerk" / "static" / "admin" / "chronikwerk-mark.svg"
    css = css_path.read_text(encoding="utf-8")
    mark = base64.b64encode(mark_path.read_bytes()).decode("ascii")
    page_css = (
        f"html, body {{ width: {spec.width}px; min-height: {spec.height}px; }}\n"
        'input[type="hidden"], dialog:not([open]) { display: none !important; }\n'
    )
    html = html.replace(
        '<link rel="stylesheet" href="/admin/static/admin.css">',
        f"<style>{css}\n{page_css}</style>",
        1,
    )
    html = html.replace(
        'src="/admin/static/chronikwerk-mark.svg"',
        f'src="data:image/svg+xml;base64,{mark}"',
    )
    return re.sub(
        r'\s*<script type="module" src="/admin/static/admin\.js"></script>',
        "",
        html,
        count=1,
    )


def _render_png(
    html: str,
    *,
    destination: Path,
    spec: ScreenshotSpec,
    work_dir: Path,
) -> None:
    """Render fixed authenticated HTML with the repository's pinned Chromium."""
    html_path = work_dir / f"{destination.stem}.html"
    rendered_path = work_dir / destination.name
    html_path.write_text(html, encoding="utf-8")
    subprocess.run(  # nosec B603
        [
            "node",
            "--input-type=module",
            "-e",
            _BROWSER_RENDER_SCRIPT,
            str(html_path),
            str(rendered_path),
            str(spec.width),
            str(spec.height),
        ],
        cwd=REPO_ROOT,
        check=True,
    )
    rendered_path.replace(destination)


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


def render(captured_at: datetime) -> None:
    """Generate all previews and atomically replace the matching manifest."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="chronikwerk-doc-screenshots-") as temporary:
        work_dir = Path(temporary).resolve()
        os.environ.setdefault("XDG_CACHE_HOME", str(work_dir / "cache"))
        app = create_app(_settings(work_dir))
        app.state.process_started_at = captured_at
        with (
            patch.object(_page_routes, "datetime", _fixed_datetime(captured_at)),
            TestClient(app) as client,
        ):
            for spec in SCREENSHOTS:
                html = _authenticated_html(client, spec=spec)
                _render_png(
                    _inline_assets(html, spec=spec),
                    destination=OUTPUT_DIR / spec.filename,
                    spec=spec,
                    work_dir=work_dir,
                )
        manifest = _manifest(captured_at)
        manifest_tmp = work_dir / "manifest.json"
        manifest_tmp.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        manifest_tmp.replace(MANIFEST_PATH)


def main() -> int:
    """Expose deterministic screenshot generation through a small command-line interface."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--captured-at",
        help="UTC ISO-8601 timestamp used for rendered process and refresh times",
    )
    args = parser.parse_args()
    render(_captured_at(args.captured_at))
    print(f"rendered {len(SCREENSHOTS)} screenshots and {MANIFEST_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
