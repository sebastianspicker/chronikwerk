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
from typing import TYPE_CHECKING
from unittest.mock import patch

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

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
    settings_sources = sorted((REPO_ROOT / "src" / "chronikwerk" / "config").glob("_settings*.py"))
    templates = sorted((REPO_ROOT / "src" / "chronikwerk" / "templates" / "admin").glob("*.html"))
    return sorted({*explicit, *admin_modules, *settings_sources, *templates})


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
    from chronikwerk.config.settings import Settings

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
    result = subprocess.run(  # nosec B603
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
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        error = result.stderr.lower()
        if "executable doesn't exist" in error or "executable does not exist" in error:
            raise RuntimeError(
                "pinned Playwright Chromium is unavailable; run `npx playwright install chromium`"
            )
        raise RuntimeError("Playwright Chromium renderer failed")
    rendered_path.replace(destination)


def _manifest(captured_at: datetime, *, output_dir: Path) -> dict[str, object]:
    """Describe source provenance, renderer limits, and exact output checksums."""
    from chronikwerk._version import __version__

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
                "sha256": _sha256(output_dir / spec.filename),
            }
            for spec in SCREENSHOTS
        ],
    }


def _write_manifest(manifest: dict[str, object], destination: Path) -> None:
    """Write the canonical manifest format used for generated screenshot evidence."""
    destination.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def render(captured_at: datetime, *, output_dir: Path = OUTPUT_DIR) -> Path:
    """Generate all previews and atomically replace the matching manifest."""
    from fastapi.testclient import TestClient

    from chronikwerk.app.admin import _page_routes
    from chronikwerk.app.server import create_app

    output_dir.mkdir(parents=True, exist_ok=True)
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
                    destination=output_dir / spec.filename,
                    spec=spec,
                    work_dir=work_dir,
                )
        manifest = _manifest(captured_at, output_dir=output_dir)
        manifest_tmp = work_dir / "manifest.json"
        _write_manifest(manifest, manifest_tmp)
        manifest_tmp.replace(output_dir / "manifest.json")
    return output_dir / "manifest.json"


def _read_manifest(path: Path) -> dict[str, object]:
    """Load a screenshot manifest or report a concise verification failure."""
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read manifest: {path.relative_to(REPO_ROOT)}") from exc
    if not isinstance(manifest, dict):
        raise RuntimeError(f"invalid manifest object: {path.relative_to(REPO_ROOT)}")
    return manifest


def _manifest_timestamp(manifest: dict[str, object]) -> datetime:
    """Read the checked-in capture instant required for reproducible rendering."""
    captured_at = manifest.get("rendered_at_utc")
    if not isinstance(captured_at, str):
        raise RuntimeError("manifest rendered_at_utc must be a timestamp")
    try:
        return _captured_at(captured_at)
    except ValueError as exc:
        raise RuntimeError("manifest rendered_at_utc must include a timezone") from exc


def _normalized_manifest(
    path: Path,
    *,
    ignored_fields: frozenset[str] = frozenset(),
) -> bytes:
    """Normalize stable manifest data while omitting checkout-relative provenance."""
    manifest = _read_manifest(path)
    for field in ignored_fields:
        manifest.pop(field, None)
    return (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _verification_errors(rendered_dir: Path) -> list[str]:
    """Return stable mismatches between checked-in and isolated rendered evidence."""
    errors: list[str] = []
    for spec in SCREENSHOTS:
        expected = OUTPUT_DIR / spec.filename
        rendered = rendered_dir / spec.filename
        if not expected.is_file():
            errors.append(f"missing checked-in PNG: {spec.filename}")
        elif not rendered.is_file() or _sha256(rendered) != _sha256(expected):
            errors.append(f"PNG mismatch: {spec.filename}")
    ignored_manifest_fields = frozenset({"base_revision"})
    if _normalized_manifest(
        rendered_dir / "manifest.json",
        ignored_fields=ignored_manifest_fields,
    ) != _normalized_manifest(
        MANIFEST_PATH,
        ignored_fields=ignored_manifest_fields,
    ):
        errors.append("manifest mismatch")
    return errors


def verify() -> None:
    """Render into a temporary directory and fail when tracked evidence differs."""
    manifest = _read_manifest(MANIFEST_PATH)
    captured_at = _manifest_timestamp(manifest)
    with tempfile.TemporaryDirectory(prefix="chronikwerk-doc-screenshot-verify-") as temporary:
        rendered_dir = Path(temporary).resolve()
        render(captured_at, output_dir=rendered_dir)
        errors = _verification_errors(rendered_dir)
    if errors:
        raise RuntimeError("; ".join(errors))


def main() -> int:
    """Expose deterministic screenshot generation through a small command-line interface."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--captured-at",
        help="UTC ISO-8601 timestamp used for rendered process and refresh times",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="render into a temporary directory and compare against checked-in evidence",
    )
    args = parser.parse_args()
    if args.verify and args.captured_at:
        parser.error("--captured-at cannot be combined with --verify")
    if args.verify:
        try:
            verify()
        except RuntimeError as exc:
            print(f"screenshot-verify: FAIL: {exc}")
            return 1
        print(f"screenshot-verify: OK ({len(SCREENSHOTS)} screenshots)")
        return 0
    render(_captured_at(args.captured_at))
    print(f"rendered {len(SCREENSHOTS)} screenshots and {MANIFEST_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
