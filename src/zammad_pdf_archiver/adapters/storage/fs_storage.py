"""Project module."""
from __future__ import annotations

import os
import tempfile
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
        return
    finally:
        os.close(fd)


def _validate_and_prepare(target_path: Path, storage_root: Path) -> tuple[Path, Path]:
    """Validate path safety and ensure parent directory exists. Returns (target, parent)."""
    target = Path(target_path)
    parent = target.parent
    # Bug #13/#20: validate path and symlinks before any directory creation.
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
        # Bug #40: always set permissions (e.g. when overwriting existing file).
        os.fchmod(f.fileno(), 0o640)
        if fsync:
            os.fsync(f.fileno())

    if fsync:
        _fsync_dir_best_effort(parent)


def write_atomic_bytes(
    target_path: Path,
    data: bytes,
    *,
    storage_root: Path,
    fsync: bool = True,
) -> None:
    """Durably replace *target_path* without exposing a partially written file."""
    target, parent = _validate_and_prepare(target_path, storage_root)
    fd: int | None = None
    tmp_path: Path | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(dir=parent, prefix=f".{target.name}.tmp-")
        tmp_path = Path(tmp_name)
        with os.fdopen(fd, "wb") as stream:
            fd = None
            stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), 0o640)
            if fsync:
                os.fsync(stream.fileno())
        os.replace(tmp_path, target)
        tmp_path = None
        if fsync:
            _fsync_dir_best_effort(parent)
    finally:
        if fd is not None:
            os.close(fd)
        if tmp_path is not None:
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass


def _reject_symlinks_under_root(root: Path, target_dir: Path) -> None:
    """
    Reject target_dir if it traverses a symlink under root (best-effort).
    Note: TOCTOU race is possible (symlink created between check and write).
    """
    root_path = Path(root).absolute()
    dir_path = Path(target_dir).absolute()
    root_resolved = root_path.resolve(strict=False)
    dir_resolved = dir_path.resolve(strict=False)
    ensure_within_root(root_resolved, dir_resolved)

    try:
        relative = dir_path.relative_to(root_path)
    except Exception as exc:  # pragma: no cover  # pylint: disable=broad-exception-caught
        raise ValueError("target path escapes root") from exc

    current = root_path
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
        if src.parent != dst.parent:
            _fsync_dir_best_effort(src.parent)
        _fsync_dir_best_effort(dst.parent)


def remove_file_within_root(
    path: Path,
    *,
    storage_root: Path,
    fsync: bool = True,
    missing_ok: bool = False,
) -> None:
    """Remove a non-symlink file under *storage_root* and persist the directory entry."""
    path = Path(path)
    ensure_within_root(storage_root, path)
    _reject_symlinks_under_root(storage_root, path.parent)
    if path.is_symlink():
        raise ValueError("refusing to remove symlink under storage root")
    try:
        path.unlink()
    except FileNotFoundError:
        if not missing_ok:
            raise
        return
    if fsync:
        _fsync_dir_best_effort(path.parent)
