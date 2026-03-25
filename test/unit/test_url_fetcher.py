"""Unit tests for the safe URL fetcher used by WeasyPrint."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# We test the logic of _SafeURLFetcher without requiring weasyprint at import time.
# The weasyprint.urls imports are lazy (inside fetch()), so we mock them.


def _make_fetcher(template_root: Path):
    """Import and construct a _SafeURLFetcher."""
    from zammad_pdf_archiver.adapters.pdf.url_fetcher import _SafeURLFetcher

    return _SafeURLFetcher(template_root)


def _patch_weasyprint_urls():
    """
    Return a context manager that patches the weasyprint.urls imports
    inside the fetch() method with lightweight stubs.
    """

    class FatalURLFetchingError(Exception):
        pass

    class URLFetcherResponse:
        def __init__(self, url: str, body: bytes, status: int = 200):
            self.url = url
            self.body = body
            self.status = status

    class URLFetcher:
        def __init__(self, allowed_protocols=()):
            self.allowed_protocols = allowed_protocols

        def fetch(self, url, headers=None):
            return URLFetcherResponse(url=url, body=b"data-content", status=200)

    mock_module = MagicMock()
    mock_module.FatalURLFetchingError = FatalURLFetchingError
    mock_module.URLFetcherResponse = URLFetcherResponse
    mock_module.URLFetcher = URLFetcher

    return patch.dict("sys.modules", {"weasyprint.urls": mock_module}), FatalURLFetchingError


# -- data: URLs pass through -------------------------------------------------------


def test_data_url_passes_through(tmp_path: Path) -> None:
    ctx, _ = _patch_weasyprint_urls()
    with ctx:
        fetcher = _make_fetcher(tmp_path)
        result = fetcher.fetch("data:text/plain;base64,SGVsbG8=")
    assert result.body == b"data-content"


# -- file:// URLs within template root are allowed ----------------------------------


def test_file_url_within_template_root_allowed(tmp_path: Path) -> None:
    asset = tmp_path / "styles.css"
    asset.write_bytes(b"body { color: red; }")

    ctx, _ = _patch_weasyprint_urls()
    with ctx:
        fetcher = _make_fetcher(tmp_path)
        result = fetcher.fetch(f"file://{asset}")
    assert result.body == b"body { color: red; }"


def test_file_url_in_subdirectory_allowed(tmp_path: Path) -> None:
    sub = tmp_path / "assets"
    sub.mkdir()
    asset = sub / "logo.png"
    asset.write_bytes(b"\x89PNG")

    ctx, _ = _patch_weasyprint_urls()
    with ctx:
        fetcher = _make_fetcher(tmp_path)
        result = fetcher.fetch(f"file://{asset}")
    assert result.body == b"\x89PNG"


# -- file:// URLs outside template root are blocked ---------------------------------


def test_file_url_outside_template_root_blocked(tmp_path: Path) -> None:
    template_root = tmp_path / "templates"
    template_root.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("sensitive")

    ctx, FatalError = _patch_weasyprint_urls()
    with ctx:
        fetcher = _make_fetcher(template_root)
        with pytest.raises(FatalError, match="outside template root"):
            fetcher.fetch(f"file://{secret}")


def test_file_url_traversal_blocked(tmp_path: Path) -> None:
    template_root = tmp_path / "templates"
    template_root.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("sensitive")

    ctx, FatalError = _patch_weasyprint_urls()
    with ctx:
        fetcher = _make_fetcher(template_root)
        with pytest.raises(FatalError, match="outside template root"):
            fetcher.fetch(f"file://{template_root}/../secret.txt")


# -- http/https URLs are blocked ----------------------------------------------------


def test_http_url_blocked(tmp_path: Path) -> None:
    ctx, FatalError = _patch_weasyprint_urls()
    with ctx:
        fetcher = _make_fetcher(tmp_path)
        with pytest.raises(FatalError, match="URL scheme not allowed"):
            fetcher.fetch("http://evil.example.com/payload.js")


def test_https_url_blocked(tmp_path: Path) -> None:
    ctx, FatalError = _patch_weasyprint_urls()
    with ctx:
        fetcher = _make_fetcher(tmp_path)
        with pytest.raises(FatalError, match="URL scheme not allowed"):
            fetcher.fetch("https://evil.example.com/payload.js")


# -- file:// URL pointing to non-existent file raises --------------------------------


def test_file_url_nonexistent_file_raises(tmp_path: Path) -> None:
    ctx, FatalError = _patch_weasyprint_urls()
    with ctx:
        fetcher = _make_fetcher(tmp_path)
        with pytest.raises(FatalError, match="not a file"):
            fetcher.fetch(f"file://{tmp_path}/no_such_file.css")


# -- __call__ delegates to fetch ----------------------------------------------------


def test_callable_delegates_to_fetch(tmp_path: Path) -> None:
    asset = tmp_path / "style.css"
    asset.write_bytes(b"p{}")

    ctx, _ = _patch_weasyprint_urls()
    with ctx:
        fetcher = _make_fetcher(tmp_path)
        result = fetcher(f"file://{asset}")
    assert result.body == b"p{}"
