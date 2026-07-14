from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from zammad_pdf_archiver.adapters.storage.fs_storage import (
    _reject_symlinks_under_root,
    move_file_within_root,
    path_entry_exists,
    remove_tree_within_root,
    unlink_file_within_root,
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


# -- write_bytes cleanup on failure ----------------------------------------


def test_write_bytes_creates_file_on_success(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "sub" / "output.bin"

    write_bytes(target, b"hello", storage_root=root, fsync=False)

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


def test_write_bytes_does_not_follow_leaf_symlink(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside")
    target = root / "file.bin"
    try:
        target.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks not supported in this environment")

    with pytest.raises(ValueError, match="escapes root"):
        write_bytes(target, b"archive", storage_root=root, fsync=False)

    assert outside.read_bytes() == b"outside"


def test_write_bytes_atomically_replaces_hard_link_without_mutating_outside_file(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside")
    target = root / "file.bin"
    try:
        os.link(outside, target)
    except OSError:
        pytest.skip("hard links not supported in this environment")

    write_bytes(target, b"archive", storage_root=root, fsync=False)

    assert outside.read_bytes() == b"outside"
    assert target.read_bytes() == b"archive"


def test_write_bytes_parent_swap_cannot_redirect_outside_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    safe_parent = root / "safe"
    safe_parent.mkdir(parents=True)
    displaced_parent = root / "displaced"
    outside = tmp_path / "outside"
    outside.mkdir()
    target = safe_parent / "output.bin"

    original_open = os.open
    swapped = False

    def racing_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        if str(path).startswith(".tmp-") and dir_fd is not None and not swapped:
            safe_parent.rename(displaced_parent)
            safe_parent.symlink_to(outside, target_is_directory=True)
            swapped = True
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", racing_open)
    write_bytes(target, b"archive", storage_root=root, fsync=False)

    assert swapped is True
    assert (displaced_parent / target.name).read_bytes() == b"archive"
    assert not (outside / target.name).exists()


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


def test_move_parent_swap_cannot_redirect_outside_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    src = root / "src.bin"
    src.write_bytes(b"archive")
    safe_parent = root / "safe"
    safe_parent.mkdir()
    displaced_parent = root / "displaced"
    outside = tmp_path / "outside"
    outside.mkdir()
    dst = safe_parent / "dst.bin"

    original_replace = os.replace
    swapped = False

    def racing_replace(
        source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        nonlocal swapped
        safe_parent.rename(displaced_parent)
        safe_parent.symlink_to(outside, target_is_directory=True)
        swapped = True
        original_replace(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(os, "replace", racing_replace)
    move_file_within_root(src, dst, storage_root=root, fsync=False)

    assert swapped is True
    assert (displaced_parent / dst.name).read_bytes() == b"archive"
    assert not (outside / dst.name).exists()


def test_unlink_parent_swap_cannot_delete_outside_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    safe_parent = root / "safe"
    safe_parent.mkdir(parents=True)
    victim = safe_parent / "victim.bin"
    victim.write_bytes(b"archive")
    displaced_parent = root / "displaced"
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_victim = outside / victim.name
    outside_victim.write_bytes(b"outside")

    original_unlink = os.unlink
    swapped = False

    def racing_unlink(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *,
        dir_fd: int | None = None,
    ) -> None:
        nonlocal swapped
        if path == victim.name and dir_fd is not None and not swapped:
            safe_parent.rename(displaced_parent)
            safe_parent.symlink_to(outside, target_is_directory=True)
            swapped = True
        original_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(os, "unlink", racing_unlink)
    unlink_file_within_root(victim, storage_root=root, fsync=False)

    assert swapped is True
    assert not (displaced_parent / victim.name).exists()
    assert outside_victim.read_bytes() == b"outside"


def test_remove_tree_parent_swap_cannot_delete_outside_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    safe_parent = root / "safe"
    tree = safe_parent / "temp"
    tree.mkdir(parents=True)
    (tree / "archive.bin").write_bytes(b"archive")
    displaced_parent = root / "displaced"
    outside = tmp_path / "outside"
    outside_tree = outside / tree.name
    outside_tree.mkdir(parents=True)
    outside_file = outside_tree / "do-not-delete.bin"
    outside_file.write_bytes(b"outside")

    original_rmtree = shutil.rmtree
    swapped = False

    def racing_rmtree(*args: Any, **kwargs: Any) -> None:
        nonlocal swapped
        safe_parent.rename(displaced_parent)
        safe_parent.symlink_to(outside, target_is_directory=True)
        swapped = True
        original_rmtree(*args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", racing_rmtree)
    remove_tree_within_root(tree, storage_root=root, fsync=False)

    assert swapped is True
    assert not (displaced_parent / tree.name).exists()
    assert outside_file.read_bytes() == b"outside"


def test_remove_tree_leaf_swap_fails_closed_without_following_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    parent = root / "safe"
    tree = parent / "temp"
    tree.mkdir(parents=True)
    (tree / "archive.bin").write_bytes(b"archive")
    displaced_tree = parent / "displaced"
    outside_tree = tmp_path / "outside"
    outside_tree.mkdir()
    outside_file = outside_tree / "do-not-delete.bin"
    outside_file.write_bytes(b"outside")

    original_rmtree = shutil.rmtree
    swapped = False

    def racing_rmtree(*args: Any, **kwargs: Any) -> None:
        nonlocal swapped
        if not swapped:
            tree.rename(displaced_tree)
            tree.symlink_to(outside_tree, target_is_directory=True)
            swapped = True
        original_rmtree(*args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", racing_rmtree)
    with pytest.raises(OSError):
        remove_tree_within_root(tree, storage_root=root, fsync=False)

    assert swapped is True
    assert (displaced_tree / "archive.bin").read_bytes() == b"archive"
    assert outside_file.read_bytes() == b"outside"


def test_path_entry_exists_does_not_follow_leaf_symlink(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside")
    link = root / "backup"
    link.symlink_to(outside)

    assert path_entry_exists(link, storage_root=root) is True
