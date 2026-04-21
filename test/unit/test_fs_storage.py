from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from zammad_pdf_archiver.adapters.storage.fs_storage import (
    _reject_symlinks_under_root,
    move_file_within_root,
    write_atomic_bytes,
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


# -- write_atomic_bytes cleanup on failure ----------------------------------------


def test_write_atomic_bytes_cleans_up_temp_on_write_error(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "output.pdf"

    # Patch os.replace to simulate failure after the temp file has been written
    replace_target = "zammad_pdf_archiver.adapters.storage.fs_storage.os.replace"
    with patch(replace_target, side_effect=OSError("disk error")):
        with pytest.raises(OSError, match="disk error"):
            write_atomic_bytes(target, b"data", storage_root=root)

    # No temp files should remain
    remaining = list(root.glob(".tmp-*"))
    assert remaining == [], f"temp files not cleaned up: {remaining}"

    # Target should not exist either
    assert not target.exists()


def test_write_atomic_bytes_creates_file_on_success(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "sub" / "output.bin"

    write_atomic_bytes(target, b"hello", storage_root=root, fsync=False)

    assert target.read_bytes() == b"hello"
    assert oct(target.stat().st_mode & 0o777) == oct(0o640)


# -- write_bytes -------------------------------------------------------------------


def test_write_bytes_creates_file_with_correct_perms(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "file.bin"

    write_bytes(target, b"content", storage_root=root, fsync=False)

    assert target.read_bytes() == b"content"
    assert oct(target.stat().st_mode & 0o777) == oct(0o640)


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

    assert not src.exists()
    assert dst.read_bytes() == b"payload"


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
