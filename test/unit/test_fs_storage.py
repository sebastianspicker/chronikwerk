from __future__ import annotations

from pathlib import Path

import pytest

from test.support.checks import check
from zammad_pdf_archiver.adapters.storage.fs_storage import (
    _reject_symlinks_under_root,
    move_file_within_root,
    write_bytes,
)

# -- _reject_symlinks_under_root ------------------------------------------------


def test_reject_symlinks_under_root_allows_normal_dirs(tmp_path: Path) -> None:
    root = tmp_path / "root"
    sub = root / "a" / "b"
    sub.mkdir(parents=True)
    # Should not raise
    _reject_symlinks_under_root(root, sub)


def test_reject_symlinks_under_root_blocks_symlink_component(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    link = root / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks not supported in this environment")

    with pytest.raises(ValueError, match="escapes root"):
        _reject_symlinks_under_root(root, link / "child")


def test_reject_symlinks_under_root_blocks_symlink_leaf(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    link = root / "sym"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks not supported in this environment")

    with pytest.raises(ValueError, match="escapes root"):
        _reject_symlinks_under_root(root, link)


def test_reject_symlinks_under_root_blocks_deep_symlink(tmp_path: Path) -> None:
    root = tmp_path / "root"
    a = root / "a"
    a.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()

    link = a / "sneaky"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks not supported in this environment")

    with pytest.raises(ValueError, match="escapes root"):
        _reject_symlinks_under_root(root, link / "child")


# -- write_bytes -------------------------------------------------------------------


def test_write_bytes_creates_file_with_correct_perms(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "file.bin"

    write_bytes(target, b"content", storage_root=root, fsync=False)

    check(not not target.read_bytes() == b"content", "assertion failed")
    check(not not oct(target.stat().st_mode & 511) == oct(416), "assertion failed")


def test_write_bytes_rejects_path_outside_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "elsewhere" / "file.bin"

    with pytest.raises(ValueError, match="escapes root"):
        write_bytes(outside, b"nope", storage_root=root)


# -- move_file_within_root --------------------------------------------------------


def test_move_file_within_root_success(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    src = root / "src.bin"
    src.write_bytes(b"payload")
    dst = root / "subdir" / "dst.bin"

    move_file_within_root(src, dst, storage_root=root, fsync=False)

    check(not not not src.exists(), "assertion failed")
    check(not not dst.read_bytes() == b"payload", "assertion failed")


def test_move_file_within_root_rejects_src_outside_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"data")
    dst = root / "dst.bin"

    with pytest.raises(ValueError, match="escapes root"):
        move_file_within_root(outside, dst, storage_root=root)


def test_move_file_within_root_rejects_dst_outside_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    src = root / "src.bin"
    src.write_bytes(b"data")
    outside = tmp_path / "outside.bin"

    with pytest.raises(ValueError, match="escapes root"):
        move_file_within_root(src, outside, storage_root=root)


def test_move_file_within_root_rejects_dst_through_symlink(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    link = root / "sym"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks not supported in this environment")

    src = root / "src.bin"
    src.write_bytes(b"data")
    dst = link / "dst.bin"

    with pytest.raises(ValueError, match="escapes root"):
        move_file_within_root(src, dst, storage_root=root)
