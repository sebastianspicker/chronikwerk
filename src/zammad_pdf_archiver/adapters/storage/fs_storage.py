from __future__ import annotations

import os
from pathlib import Path

from zammad_pdf_archiver.domain.path_policy import ensure_within_root


def ensure_dir(path: Path) -> None:
    """Create path and all intermediate parents if they do not already exist."""
    Path(path).mkdir(parents=True, exist_ok=True)


def _fsync_dir_best_effort(dir_path: Path) -> None:
    """
    Best-effort directory fsync after atomic replace.

    This improves durability across crashes on POSIX filesystems. Some platforms /
    filesystems may not support fsync on directories; failures are ignored.
    """
    try:
        fd = os.open(str(dir_path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _validate_and_prepare(target_path: Path, storage_root: Path) -> tuple[Path, Path]:
    """Validate path safety and ensure parent directory exists. Returns (target, parent)."""
    target = Path(target_path)
    parent = target.parent
    # Validate containment and symlink traversal before any directory creation.
    ensure_within_root(storage_root, target)
    _reject_symlinks_under_root(storage_root, parent)
    ensure_dir(parent)
    return target, parent


def write_bytes(target_path: Path, data: bytes, *, storage_root: Path, fsync: bool = True) -> None:
    """Write data to target_path within storage_root with O_NOFOLLOW and correct permissions."""
    target, parent = _validate_and_prepare(target_path, storage_root)

    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(target), flags, 0o640)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        # Always set permissions, including when overwriting an existing file.
        os.fchmod(f.fileno(), 0o640)
        if fsync:
            os.fsync(f.fileno())

    if fsync:
        _fsync_dir_best_effort(parent)


def _reject_symlinks_under_root(root: Path, target_dir: Path) -> None:
    """
    Reject target_dir if it traverses a symlink under root (best-effort).
    Note: TOCTOU race is possible (symlink created between check and write).
    """
    root_resolved = Path(root).resolve(strict=False)
    dir_resolved = Path(target_dir).resolve(strict=False)
    ensure_within_root(root_resolved, dir_resolved)

    try:
        relative = dir_resolved.relative_to(root_resolved)
    except Exception as exc:  # pragma: no cover
        raise ValueError("target path escapes root") from exc

    current = root_resolved
    for part in relative.parts:
        current = current / part
        try:
            if current.is_symlink():
                raise ValueError("target path traverses a symlink under storage root")
        except OSError as exc:
            # If the path is unreadable, treat it as unsafe.
            raise ValueError("target path validation failed (unreadable component)") from exc


def move_file_within_root(
    src: Path,
    dst: Path,
    *,
    storage_root: Path,
    fsync: bool = True,
) -> None:
    """
    Move a file from src to dst after validating both are within storage_root and dst
    doesn't traverse symlinks.
    """
    src = Path(src)
    dst = Path(dst)

    ensure_within_root(storage_root, src)
    ensure_within_root(storage_root, dst)
    _reject_symlinks_under_root(storage_root, dst.parent)

    ensure_dir(dst.parent)
    os.replace(src, dst)

    if fsync:
        _fsync_dir_best_effort(dst.parent)
