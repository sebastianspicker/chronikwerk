#!/usr/bin/env python3
"""Validate the public Markdown inventory and local link targets."""

from __future__ import annotations

import json
import re
import struct
from pathlib import Path
from urllib.parse import unquote, urlsplit

from ci_1_helper import ci_1_helper as _sha256

REPO_ROOT = Path(__file__).resolve().parents[2]
REQUIRED_PATHS = (
    "README.md",
    "CHANGELOG.md",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "DESIGN.md",
    "PRODUCT.md",
    "RELEASE_STATUS.md",
    "SECURITY.md",
    "docs/01-architecture.md",
    "docs/02-zammad-setup.md",
    "docs/03-data-model.md",
    "docs/04-path-policy.md",
    "docs/05-pdf-rendering.md",
    "docs/06-signing-and-timestamp.md",
    "docs/07-storage.md",
    "docs/08-operations.md",
    "docs/09-security.md",
    "docs/README.md",
    "docs/adr/0004-current-architecture.md",
    "docs/adr/0005-admin-config-and-accessible-pdf.md",
    "docs/adr/0006-zammad-outbound-transport-trust-boundary.md",
    "docs/adr/0007-deterministic-release-assurance-scripts.md",
    "docs/alpha-release.md",
    "docs/admin-frontend.md",
    "docs/api.md",
    "docs/config-reference.md",
    "docs/deploy.md",
    "docs/faq.md",
    "docs/migration-to-chronikwerk.md",
    "docs/assets/brand/README.md",
    "docs/assets/brand/chronikwerk-lockup.svg",
    "docs/assets/brand/chronikwerk-mark-monochrome.svg",
    "docs/assets/brand/chronikwerk-mark.svg",
    "docs/release-checklist.md",
    "docs/screenshots/README.md",
    "docs/screenshots/manifest.json",
)
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
LOCAL_DOC_DIRECTORIES = {"archive", "audit", "source-audit"}


def _public_markdown() -> list[Path]:
    """Collect release-facing Markdown while excluding explicitly local work lanes."""
    root_docs = [
        REPO_ROOT / relative
        for relative in REQUIRED_PATHS
        if Path(relative).parent == Path(".") and Path(relative).suffix == ".md"
    ]
    nested_docs = [
        path
        for path in (REPO_ROOT / "docs").rglob("*.md")
        if not LOCAL_DOC_DIRECTORIES.intersection(path.relative_to(REPO_ROOT / "docs").parts[:-1])
    ]
    return sorted(root_docs + nested_docs)


def _png_dimensions(path: Path) -> tuple[int, int]:
    """Read PNG IHDR dimensions without trusting a renderer-specific library."""
    with path.open("rb") as handle:
        header = handle.read(24)
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG file")
    return struct.unpack(">II", header[16:24])


def _repo_path(relative: str) -> Path:
    """Resolve a manifest path and reject attempts to escape the checkout."""
    target = (REPO_ROOT / relative).resolve()
    target.relative_to(REPO_ROOT)
    return target


def _load_screenshot_manifest(relative_manifest: str) -> tuple[dict[str, object] | None, list[str]]:
    """Load the optional-looking JSON as a required, human-readable release contract."""
    try:
        payload = json.loads((REPO_ROOT / relative_manifest).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, [f"{relative_manifest}: unreadable or invalid JSON: {exc}"]
    if not isinstance(payload, dict):
        return None, [f"{relative_manifest}: root must be an object"]
    return payload, []


def _source_file_error(
    relative_manifest: str,
    relative: object,
    expected_hash: object,
) -> str | None:
    """Return one source-provenance mismatch rather than aborting the full report."""
    try:
        source = _repo_path(str(relative))
    except ValueError:
        return f"{relative_manifest}: source path escapes repository: {relative}"
    if not source.is_file():
        return f"{relative_manifest}: missing source file: {relative}"
    if _sha256(source) != expected_hash:
        return f"{relative_manifest}: stale source hash: {relative}"
    return None


def _source_file_errors(
    relative_manifest: str,
    source_files: object,
) -> list[str]:
    """Validate every declared source hash so screenshots cannot silently go stale."""
    if not isinstance(source_files, dict) or not source_files:
        return [f"{relative_manifest}: source_files must be a non-empty object"]
    return [
        error
        for relative, expected_hash in source_files.items()
        if (error := _source_file_error(relative_manifest, relative, expected_hash)) is not None
    ]


def _image_path_error(
    relative_manifest: str,
    relative: str,
) -> tuple[Path | None, str | None]:
    """Constrain manifest image entries to direct files in the screenshot directory."""
    try:
        image_path = _repo_path(f"docs/screenshots/{relative}")
    except ValueError:
        return (
            None,
            f"{relative_manifest}: image path escapes screenshot directory: {relative}",
        )
    if image_path.parent != (REPO_ROOT / "docs" / "screenshots").resolve():
        return None, f"{relative_manifest}: nested image path is not allowed: {relative}"
    if not image_path.is_file():
        return None, f"{relative_manifest}: missing image: {relative}"
    return image_path, None


def _image_file_errors(
    relative_manifest: str,
    image: dict[str, object],
    *,
    relative: str,
    image_path: Path,
) -> list[str]:
    """Compare one PNG's checksum and dimensions with its declared viewport."""
    errors: list[str] = []
    if _sha256(image_path) != image.get("sha256"):
        errors.append(f"{relative_manifest}: image checksum mismatch: {relative}")
    viewport = image.get("viewport")
    if not isinstance(viewport, dict):
        return [*errors, f"{relative_manifest}: missing viewport: {relative}"]
    try:
        dimensions = _png_dimensions(image_path)
    except ValueError as exc:
        return [*errors, f"{relative_manifest}: invalid image {relative}: {exc}"]
    if dimensions != (viewport.get("width"), viewport.get("height")):
        errors.append(
            f"{relative_manifest}: image dimensions mismatch for {relative}: "
            f"{dimensions[0]}x{dimensions[1]}"
        )
    return errors


def _image_entry_errors(relative_manifest: str, image: object) -> list[str]:
    """Validate one untrusted manifest image entry before reading its file."""
    if not isinstance(image, dict):
        return [f"{relative_manifest}: image entries must be objects"]
    relative = image.get("path")
    if not isinstance(relative, str):
        return [f"{relative_manifest}: image path must be a string"]
    image_path, path_error = _image_path_error(relative_manifest, relative)
    if path_error is not None or image_path is None:
        return [path_error or f"{relative_manifest}: invalid image path: {relative}"]
    return _image_file_errors(
        relative_manifest,
        image,
        relative=relative,
        image_path=image_path,
    )


def _image_errors(relative_manifest: str, images: object) -> list[str]:
    """Flatten image validation failures so maintainers can fix them in one pass."""
    if not isinstance(images, list) or not images:
        return [f"{relative_manifest}: images must be a non-empty list"]
    return [error for image in images for error in _image_entry_errors(relative_manifest, image)]


def _screenshot_manifest_errors() -> list[str]:
    """Enforce the versioned screenshot manifest contract used by public docs."""
    relative_manifest = "docs/screenshots/manifest.json"
    manifest, errors = _load_screenshot_manifest(relative_manifest)
    if manifest is None:
        return errors
    if manifest.get("schema_version") != 2:
        errors.append(f"{relative_manifest}: expected schema_version 2")
    errors.extend(_source_file_errors(relative_manifest, manifest.get("source_files")))
    errors.extend(_image_errors(relative_manifest, manifest.get("images")))
    return errors


def _target_path(source: Path, raw_target: str) -> Path | None:
    """Resolve only local Markdown targets; external URLs and anchors need no check."""
    target = raw_target.strip().strip("<>")
    if not target or target.startswith("#"):
        return None
    split = urlsplit(target)
    if split.scheme or split.netloc:
        return None
    relative = unquote(split.path)
    if not relative:
        return None
    return (source.parent / relative).resolve()


def _required_path_errors() -> list[str]:
    """List public documents that an alpha candidate must always carry."""
    return [
        f"missing required documentation path: {relative}"
        for relative in REQUIRED_PATHS
        if not (REPO_ROOT / relative).is_file()
    ]


def _link_errors(source: Path) -> list[str]:
    """Report broken local links without rejecting intentional external references."""
    errors: list[str] = []
    text = source.read_text(encoding="utf-8")
    for match in MARKDOWN_LINK.finditer(text):
        target = _target_path(source, match.group(1))
        if target is None:
            continue
        try:
            relative_target = target.relative_to(REPO_ROOT)
        except ValueError:
            errors.append(
                f"{source.relative_to(REPO_ROOT)}: link escapes repository: {match.group(1)}"
            )
            continue
        if not target.exists():
            errors.append(
                f"{source.relative_to(REPO_ROOT)}: missing link target: {relative_target}"
            )
    return errors


def main() -> int:
    """Run all documentation integrity checks and emit a CI-friendly exit code."""
    public_markdown = _public_markdown()
    errors = _required_path_errors()
    errors.extend(_screenshot_manifest_errors())
    for source in public_markdown:
        errors.extend(_link_errors(source))

    if errors:
        for error in errors:
            print(error)
        return 1
    print(f"docs-check: OK ({len(public_markdown)} Markdown files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
