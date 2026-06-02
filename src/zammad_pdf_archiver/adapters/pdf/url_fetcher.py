"""Safe URL fetcher for WeasyPrint: blocks file:// outside the template root."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from urllib.parse import unquote, urlparse


class _SafeURLFetcher:
    """WeasyPrint-compatible fetcher: only data: and file under template_root."""

    def __init__(self, template_root: Path) -> None:
        self._root = template_root.resolve()

    def fetch(self, url: str, headers=None):
        from weasyprint.urls import (
            FatalURLFetchingError,
            URLFetcher,
            URLFetcherResponse,
        )

        parsed = urlparse(url)
        scheme = (parsed.scheme or "").lower()
        if scheme == "data":
            return URLFetcher(allowed_protocols=("data",)).fetch(url, headers)
        if scheme == "file":
            path = self._resolve_file_url_path(parsed.path)
            self._validate_file_url_path(path, url, FatalURLFetchingError)
            return _file_url_response(url, path, URLFetcherResponse)
        raise FatalURLFetchingError(f"URL scheme not allowed: {scheme!r}")

    def __call__(self, url: str, *args, **kwargs):
        headers = kwargs.get("headers") or kwargs.get("http_headers")
        return self.fetch(url, headers=headers)

    def _resolve_file_url_path(self, raw_path: str) -> Path:
        path = Path(unquote(raw_path))
        if path.is_absolute():
            return path.resolve()
        return (self._root / path).resolve()

    def _validate_file_url_path(self, path: Path, url: str, fatal_error: type[Exception]) -> None:
        try:
            if self._root not in path.parents and path != self._root:
                raise fatal_error(f"file URL outside template root: {url!r}")
            if not path.is_file():
                raise fatal_error(f"file URL not a file: {url!r}")
        except fatal_error:
            raise
        except (OSError, ValueError) as e:  # resolve() / is_file() failures
            raise fatal_error(f"invalid file URL: {url!r}") from e


def _file_url_response(url: str, path: Path, response_type):
    body = path.read_bytes()
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return response_type(
        url=url,
        body=body,
        headers={"Content-Type": mime_type},
        status=200,
    )
