"""Verify packaged administration assets through their public routes."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from chronikwerk.web.admin import _page_routes
from chronikwerk.web.app import create_app
from tests.support.settings_factory import make_settings


def test_admin_assets_are_served_from_the_reconstructed_package(tmp_path) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={
            "admin": {
                "enabled": True,
                "access_token": "admin-token",
                "state_dir": str(tmp_path / "admin-state"),
            }
        },
    )

    client = TestClient(create_app(settings))
    css = client.get("/admin/static/admin.css")
    javascript = client.get("/admin/static/admin.js")
    mark = client.get("/admin/static/chronikwerk-mark.svg")
    fonts = [
        client.get(f"/admin/static/atkinson-hyperlegible-{name}.woff2") for name in ("next", "mono")
    ]

    assert css.status_code == 200
    assert css.headers["content-type"].startswith("text/css")
    assert b":root" in css.content
    assert javascript.status_code == 200
    assert "javascript" in javascript.headers["content-type"]
    assert b"addEventListener" in javascript.content
    assert mark.status_code == 200
    assert mark.headers["content-type"] == "image/svg+xml"
    assert b"<svg" in mark.content
    for font in fonts:
        assert font.status_code == 200
        assert font.headers["content-type"] == "font/woff2"
        assert font.content.startswith(b"wOF2")


def test_only_fixed_packaged_assets_are_cached(tmp_path) -> None:
    """Cache package reads while preserving public responses and route confinement."""
    settings = make_settings(
        str(tmp_path),
        overrides={
            "admin": {
                "enabled": True,
                "access_token": "admin-token",
                "state_dir": str(tmp_path / "admin-state"),
            }
        },
    )
    client = TestClient(create_app(settings))
    _page_routes._packaged_assets.cache_clear()
    try:
        with patch.object(
            _page_routes.resources, "files", wraps=_page_routes.resources.files
        ) as files:
            for name in (
                "admin.css",
                "admin.js",
                "chronikwerk-mark.svg",
                "atkinson-hyperlegible-next.woff2",
                "atkinson-hyperlegible-mono.woff2",
            ):
                first = client.get(f"/admin/static/{name}")
                second = client.get(f"/admin/static/{name}")
                assert first.status_code == second.status_code == 200
                assert first.content == second.content
                assert first.headers["cache-control"] == "no-store"
                assert second.headers["cache-control"] == "no-store"
            assert files.call_count == 1
            assert client.get("/admin/static/other.js").status_code == 404
            assert files.call_count == 1
    finally:
        _page_routes._packaged_assets.cache_clear()
