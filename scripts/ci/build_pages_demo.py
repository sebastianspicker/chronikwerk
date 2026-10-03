#!/usr/bin/env python3
"""Assemble the isolated mock-data administration demo for GitHub Pages."""

from __future__ import annotations

import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE = REPO_ROOT / "demo" / "site"
OUTPUT = REPO_ROOT / "build" / "pages-demo"
SCREENSHOTS = REPO_ROOT / "docs" / "screenshots"
PACKAGED_ASSETS = {
    REPO_ROOT / "src/chronikwerk/web/static/admin/admin.css": "assets/admin.css",
    REPO_ROOT / "src/chronikwerk/web/static/admin/chronikwerk-mark.svg": (
        "assets/chronikwerk-mark.svg"
    ),
    REPO_ROOT / "src/chronikwerk/web/static/admin/atkinson-hyperlegible-next.woff2": (
        "assets/atkinson-hyperlegible-next.woff2"
    ),
    REPO_ROOT / "src/chronikwerk/web/static/admin/atkinson-hyperlegible-mono.woff2": (
        "assets/atkinson-hyperlegible-mono.woff2"
    ),
    REPO_ROOT / "src/chronikwerk/web/static/admin/atkinson-hyperlegible-OFL.txt": (
        "assets/atkinson-hyperlegible-OFL.txt"
    ),
}
TOUR_SCREENSHOTS = (
    "admin-overview.png",
    "admin-jobs.png",
    "admin-ticket.png",
    "admin-configuration.png",
    "admin-revisions.png",
    "admin-login.png",
)
REQUIRED_SOURCE_FILES = (
    "index.html",
    "jobs.html",
    "job.html",
    "configuration.html",
    "revisions.html",
    "tour.html",
    "assets/demo.css",
    "assets/demo.js",
)


def _validate_source() -> None:
    """Fail before replacing output when the maintained demo source is incomplete."""
    missing = [relative for relative in REQUIRED_SOURCE_FILES if not (SOURCE / relative).is_file()]
    missing.extend(
        source.relative_to(REPO_ROOT).as_posix()
        for source in PACKAGED_ASSETS
        if not source.is_file()
    )
    missing.extend(
        (SCREENSHOTS / name).relative_to(REPO_ROOT).as_posix()
        for name in TOUR_SCREENSHOTS
        if not (SCREENSHOTS / name).is_file()
    )
    if missing:
        raise SystemExit(f"pages-demo-build: missing source files: {', '.join(missing)}")
    _reject_source_symlinks()


def _reject_source_symlinks() -> None:
    """Keep the static artifact confined to maintained files."""
    symlinks = [
        path.relative_to(REPO_ROOT).as_posix() for path in SOURCE.rglob("*") if path.is_symlink()
    ]
    if symlinks:
        raise SystemExit(f"pages-demo-build: symlinks are not allowed: {', '.join(symlinks)}")


def main() -> int:
    """Build a self-contained project-site artifact beneath the ignored build tree."""
    _validate_source()
    expected_parent = (REPO_ROOT / "build").resolve()
    if OUTPUT.resolve().parent != expected_parent:
        raise SystemExit("pages-demo-build: refusing an unexpected output path")
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    shutil.copytree(SOURCE, OUTPUT)
    for source, relative_target in PACKAGED_ASSETS.items():
        target = OUTPUT / relative_target
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    tour_dir = OUTPUT / "assets" / "tour"
    tour_dir.mkdir(parents=True, exist_ok=True)
    for name in TOUR_SCREENSHOTS:
        shutil.copyfile(SCREENSHOTS / name, tour_dir / name)
    (OUTPUT / ".nojekyll").write_text("", encoding="utf-8")
    file_count = sum(path.is_file() for path in OUTPUT.rglob("*"))
    print(f"pages-demo-build: wrote {file_count} files to {OUTPUT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
