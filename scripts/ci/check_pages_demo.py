#!/usr/bin/env python3
"""Validate the built GitHub Pages demo as a static, mock-only artifact."""

from __future__ import annotations

import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT = REPO_ROOT / "build" / "pages-demo"
PACKAGED_CSS = REPO_ROOT / "src/chronikwerk/web/static/admin/admin.css"
REQUIRED_FILES = (
    ".nojekyll",
    "index.html",
    "jobs.html",
    "job.html",
    "configuration.html",
    "revisions.html",
    "tour.html",
    "assets/admin.css",
    "assets/chronikwerk-mark.svg",
    "assets/atkinson-hyperlegible-next.woff2",
    "assets/atkinson-hyperlegible-mono.woff2",
    "assets/atkinson-hyperlegible-OFL.txt",
    "assets/demo.css",
    "assets/demo.js",
    "assets/tour/admin-overview.png",
    "assets/tour/admin-jobs.png",
    "assets/tour/admin-ticket.png",
    "assets/tour/admin-configuration.png",
    "assets/tour/admin-revisions.png",
    "assets/tour/admin-login.png",
)
FONT_FILES = frozenset(
    {
        "assets/atkinson-hyperlegible-next.woff2",
        "assets/atkinson-hyperlegible-mono.woff2",
        "assets/atkinson-hyperlegible-OFL.txt",
    }
)
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
FORBIDDEN_TEXT = (
    "/admin/",
    "fetch(",
    "xmlhttprequest",
    "websocket",
    "eventsource",
    "credentials:",
    "zammad_url",
    "access_token",
    "webhook_secret",
)


class LinkCollector(HTMLParser):
    """Collect local navigation and asset references from one HTML document."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []
        self.has_main = False
        self.has_nav = False
        self.has_viewport = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Record structural elements and link-bearing attributes."""
        values = dict(attrs)
        if tag == "main":
            self.has_main = True
        elif tag == "nav":
            self.has_nav = True
        elif tag == "meta" and values.get("name") == "viewport":
            self.has_viewport = True
        for name in ("href", "src"):
            if (value := values.get(name)) is not None:
                self.links.append(value)


def _link_error(source: Path, raw_link: str) -> str | None:
    """Return a precise error for unsafe or missing static references."""
    link = raw_link.strip()
    if not link or link.startswith("#"):
        return None
    split = urlsplit(link)
    if split.scheme or split.netloc or link.startswith("/"):
        return f"{source.name}: non-relative reference: {raw_link}"
    target = (source.parent / unquote(split.path)).resolve()
    try:
        target.relative_to(OUTPUT.resolve())
    except ValueError:
        return f"{source.name}: reference escapes artifact: {raw_link}"
    if not target.is_file():
        return f"{source.name}: missing reference: {raw_link}"
    return None


def _html_errors(path: Path) -> list[str]:
    """Check one page for structure, disclosure, and safe static links."""
    text = path.read_text(encoding="utf-8")
    lowered = text.lower()
    parser = LinkCollector()
    parser.feed(text)
    errors = [error for link in parser.links if (error := _link_error(path, link)) is not None]
    if '<html lang="en-GB">' not in text:
        errors.append(f"{path.name}: expected lang=en-GB")
    if not parser.has_main or not parser.has_nav or not parser.has_viewport:
        errors.append(f"{path.name}: missing main, nav, or viewport structure")
    if "synthetic" not in lowered or "no backend" not in lowered:
        errors.append(f"{path.name}: missing mock-data and no-backend disclosure")
    return errors


def _css_errors(path: Path) -> list[str]:
    """Reject resource loading that would make the artifact non-self-contained."""
    lowered = path.read_text(encoding="utf-8").lower()
    return [
        f"{path.relative_to(OUTPUT)}: forbidden CSS resource {forbidden!r}"
        for forbidden in ("@import", "http://", "https://", "url(/")
        if forbidden in lowered
    ]


def _png_errors(path: Path) -> list[str]:
    """Require the captured tour files to be real PNG images."""
    if path.read_bytes()[:8] != PNG_SIGNATURE:
        return [f"{path.relative_to(OUTPUT)}: not a PNG file"]
    return []


def _script_or_markup_errors(path: Path) -> list[str]:
    """Reject production endpoints, networking, and credential-shaped content."""
    if path.suffix.lower() not in {".html", ".js"}:
        return []
    lowered = path.read_text(encoding="utf-8").lower()
    return [
        f"{path.relative_to(OUTPUT)}: forbidden text {forbidden!r}"
        for forbidden in FORBIDDEN_TEXT
        if forbidden in lowered
    ]


def _file_errors(path: Path) -> list[str]:
    """Validate one artifact file's type, safety, and resource boundaries."""
    if path.is_symlink():
        return [f"symlink is not allowed: {path.relative_to(OUTPUT)}"]
    suffix = path.suffix.lower()
    if path.relative_to(OUTPUT).as_posix() in FONT_FILES:
        return []
    if suffix not in {".html", ".css", ".js", ".svg", ".png", ""}:
        return [f"unexpected artifact type: {path.relative_to(OUTPUT)}"]
    if suffix == ".css":
        return _css_errors(path)
    if suffix == ".png":
        return _png_errors(path)
    return _script_or_markup_errors(path)


def _artifact_errors() -> list[str]:
    """Return all violations without hiding later failures behind the first one."""
    errors = [
        f"missing artifact file: {relative}"
        for relative in REQUIRED_FILES
        if not (OUTPUT / relative).is_file()
    ]
    if errors:
        return errors
    paths = [path for path in OUTPUT.rglob("*") if path.is_file()]
    for path in paths:
        errors.extend(_file_errors(path))
    for path in sorted(OUTPUT.glob("*.html")):
        errors.extend(_html_errors(path))
    if (OUTPUT / "assets/admin.css").read_bytes() != PACKAGED_CSS.read_bytes():
        errors.append("assets/admin.css does not match the packaged administration stylesheet")
    return errors


def main() -> int:
    """Print every demo-safety failure and return a shell-friendly status."""
    if not OUTPUT.is_dir():
        print("pages-demo-check: build/pages-demo is missing", file=sys.stderr)
        return 1
    errors = _artifact_errors()
    for error in errors:
        print(f"pages-demo-check: {error}", file=sys.stderr)
    if errors:
        return 1
    print(f"pages-demo-check: OK ({len(REQUIRED_FILES)} required files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
