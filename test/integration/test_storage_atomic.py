from __future__ import annotations

from pathlib import Path

import pytest

from test.support.checks import check
from zammad_pdf_archiver.adapters.storage import move_file_within_root, write_bytes


def test_write_bytes_creates_dirs_and_writes_contents(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b" / "payload.bin"
    data = b"\x00hello\xff"

    write_bytes(target, data, storage_root=tmp_path)

    check(not not target.exists(), "assertion failed")
    check(not not target.read_bytes() == data, "assertion failed")


def test_storage_writes_use_restrictive_file_mode(tmp_path: Path) -> None:
    """Written files use 0o640 (no world read/write)."""
    target = tmp_path / "f.bin"
    write_bytes(target, b"y", storage_root=tmp_path, fsync=False)
    mode = target.stat().st_mode & 0o777
    check(not not mode == 416, f"expected 0o640, got {oct(mode)}")


def test_write_bytes_overwrites_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "payload.bin"
    target.write_bytes(b"old")

    data = b"new-data"
    write_bytes(target, data, storage_root=tmp_path)

    check(not not target.read_bytes() == data, "assertion failed")


def test_move_file_within_root_replaces_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "payload.bin"
    source = tmp_path / ".tmp-source"
    target.write_bytes(b"old")
    source.write_bytes(b"new-data")

    move_file_within_root(source, target, storage_root=tmp_path)

    check(not not target.read_bytes() == b"new-data", "assertion failed")
    check(not not not source.exists(), "assertion failed")


def test_storage_writes_reject_paths_outside_storage_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    target = outside / "payload.bin"
    with pytest.raises(ValueError, match="escapes root"):
        write_bytes(target, b"x", storage_root=root)


def test_storage_writes_reject_symlink_traversal_under_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    link = root / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are not supported in this environment")

    target = link / "payload.bin"
    with pytest.raises(ValueError, match="symlink|escapes root"):
        write_bytes(target, b"x", storage_root=root)
