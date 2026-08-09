"""Render deterministic administration screenshots from synthetic application state."""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from source import (
    _BROWSER_RENDER_SCRIPT,
    ACCESS_TOKEN,
    MANIFEST_PATH,
    OUTPUT_DIR,
    REPO_ROOT,
    SCREENSHOTS,
    WEBHOOK_SECRET,
    ScreenshotSpec,
    _manifest,
)

from chronikwerk.app.admin import _page_routes
from chronikwerk.app.server import create_app
from chronikwerk.config.settings import Settings


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
