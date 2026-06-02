"""NFR7: Single process; Docker and systemd deployment support."""

from __future__ import annotations

from pathlib import Path

from test.support.checks import check
from test.support.credentials import fake_credential
from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.server import create_app


def test_nfr7_app_creates_with_settings(tmp_path: Path) -> None:
    """NFR7: create_app must run with settings (single-process entry)."""
    settings = make_settings(
        str(tmp_path), overrides={"zammad": {"api_token": fake_credential("tok")}}
    )
    app = create_app(settings)
    check(not app.state.settings is not settings, "assertion failed")


def test_nfr7_dockerfile_exists() -> None:
    """NFR7: Dockerfile must exist for container deployment."""
    repo_root = Path(__file__).resolve().parents[2]
    dockerfile = repo_root / "Dockerfile"
    check(not not dockerfile.is_file(), "Dockerfile required for deployment")
    content = dockerfile.read_text()
    check(not not ("python" in content.lower() or "uvicorn" in content.lower()), "assertion failed")
    check(not 'uv pip install --no-cache-dir ".[redis]"' not in content, "assertion failed")
