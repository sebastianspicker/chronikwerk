"""Verifies managed-configuration filesystem and bounded-I/O hardening."""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from chronikwerk.config.managed import ManagedConfigError, ManagedConfigStore


def test_portable_managed_state_paths_reject_unsafe_files_and_preserve_regular_data(
    tmp_path: Path, monkeypatch
) -> None:
    """Exercise the non-POSIX fallback safeguards without requiring another platform."""
    store = ManagedConfigStore(tmp_path / "admin")
    monkeypatch.setattr("chronikwerk.config._managed_io.os.name", "nt")

    assert store._read_current_path() is None
    store.overlay_path.write_bytes(b'{"overlay": {}}')
    assert store._read_current_path() == b'{"overlay": {}}'

    store.overlay_path.unlink()
    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    store.overlay_path.symlink_to(target)
    with pytest.raises(ManagedConfigError, match="must not be a symlink"):
        store._read_current_path()


def test_portable_revision_reads_and_writes_are_bounded_and_atomic(
    tmp_path: Path, monkeypatch
) -> None:
    """Keep the fallback implementation from accepting oversized or non-file revisions."""
    store = ManagedConfigStore(tmp_path / "admin")
    monkeypatch.setattr("chronikwerk.config._managed_io.os.name", "nt")
    revision = store.revisions_dir / "revision.json"

    with pytest.raises(ManagedConfigError, match="Revision not found"):
        store._read_revision_path(revision)
    revision.mkdir()
    with pytest.raises(ManagedConfigError, match="unsafe"):
        store._read_revision_path(revision)
    revision.rmdir()
    revision.write_bytes(b"x" * (256 * 1024 + 1))
    with pytest.raises(ManagedConfigError, match="exceeds 256 KiB"):
        store._read_revision_path(revision)

    monkeypatch.undo()
    store._atomic_write(store.overlay_path, {"overlay": {"pdf": {"max_articles": 1}}})
    assert json.loads(store.overlay_path.read_text(encoding="utf-8")) == {
        "overlay": {"pdf": {"max_articles": 1}}
    }
    assert store.overlay_path.stat().st_mode & 0o777 == 0o600


def test_managed_io_rejects_non_regular_or_oversized_state_files(tmp_path: Path) -> None:
    store = ManagedConfigStore(tmp_path / "admin")
    store.overlay_path.mkdir()
    with pytest.raises(ManagedConfigError, match="not found or unsafe"):
        store._read_current_bytes()
    store.overlay_path.rmdir()
    store.overlay_path.write_bytes(b"x" * (256 * 1024 + 1))
    with pytest.raises(ManagedConfigError, match="exceeds 256 KiB"):
        store._read_current_bytes()


@pytest.mark.skipif(os.name != "posix", reason="POSIX no-follow file opens required")
def test_managed_io_fails_closed_when_open_detects_a_symlink_race(
    tmp_path: Path, monkeypatch
) -> None:
    """An O_NOFOLLOW failure while opening the current file is never retried unsafely."""
    store = ManagedConfigStore(tmp_path / "admin")
    store.overlay_path.write_text('{"overlay": {}}', encoding="utf-8")
    original_open = os.open

    def reject_raced_current_file(path, flags, *args, **kwargs):
        if path == store.overlay_path.name and "dir_fd" in kwargs:
            raise OSError(errno.ELOOP, "symlink substituted during open")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr("chronikwerk.config._managed_io.os.open", reject_raced_current_file)

    with pytest.raises(ManagedConfigError, match="not found or unsafe"):
        store._read_current_bytes()


@pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor reads required")
def test_managed_io_rejects_file_growth_after_the_initial_size_check(
    tmp_path: Path, monkeypatch
) -> None:
    """Bound the read itself, not just the potentially stale descriptor metadata."""
    store = ManagedConfigStore(tmp_path / "admin")
    store.overlay_path.write_bytes(b"x" * (256 * 1024 + 1))
    overlay_stat = store.overlay_path.stat()
    original_fstat = os.fstat

    def stale_file_size(fd: int):
        result = original_fstat(fd)
        if result.st_ino == overlay_stat.st_ino:
            return SimpleNamespace(st_mode=result.st_mode, st_size=0)
        return result

    monkeypatch.setattr("chronikwerk.config._managed_io.os.fstat", stale_file_size)

    with pytest.raises(ManagedConfigError, match="exceeds 256 KiB"):
        store._read_current_bytes()


@pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor pruning required")
def test_prune_leaves_an_entry_that_disappears_before_its_stat(tmp_path: Path, monkeypatch) -> None:
    """A concurrent deletion is benign and must not turn pruning into a failed stage."""
    store = ManagedConfigStore(tmp_path / "admin")
    first = store.stage(
        {"pdf": {"max_articles": 100}},
        expected_revision=store.current_revision(),
        request_id="first",
    )
    second = store.stage(
        {"pdf": {"max_articles": 50}},
        expected_revision=first["revision"],
        request_id="second",
    )
    original_stat = os.stat
    vanished_name = f"{first['revision']}.json"

    def stat_vanished_entry(path, *args, **kwargs):
        if path == vanished_name and "dir_fd" in kwargs:
            raise FileNotFoundError(errno.ENOENT, "revision disappeared")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr("chronikwerk.config._managed_io.os.stat", stat_vanished_entry)
    store._prune_revision_files({f"{second['revision']}.json"})

    assert (store.revisions_dir / vanished_name).exists()
    assert (store.revisions_dir / f"{second['revision']}.json").exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX atomic replace hardening required")
def test_atomic_write_refuses_a_symlink_target_without_touching_its_destination(
    tmp_path: Path,
) -> None:
    store = ManagedConfigStore(tmp_path / "admin")
    replacement_target = tmp_path / "replacement.json"
    replacement_target.write_text('{"preserve": true}', encoding="utf-8")
    store.overlay_path.symlink_to(replacement_target)

    with pytest.raises(ManagedConfigError, match="Refusing to replace symlink"):
        store._atomic_write(store.overlay_path, {"overlay": {"pdf": {"max_articles": 1}}})

    assert store.overlay_path.is_symlink()
    assert replacement_target.read_text(encoding="utf-8") == '{"preserve": true}'


def test_managed_io_portable_directory_checks_reject_symlinks_and_files(
    tmp_path: Path, monkeypatch
) -> None:
    store = ManagedConfigStore(tmp_path / "admin")
    portable_directory = tmp_path / "portable-state"
    regular_file = tmp_path / "not-a-directory"
    regular_file.write_text("state", encoding="utf-8")
    target = tmp_path / "target"
    target.mkdir()
    symlink = tmp_path / "state-link"
    symlink.symlink_to(target, target_is_directory=True)

    monkeypatch.setattr("chronikwerk.config._managed_io.os.name", "nt")
    store._ensure_directory(portable_directory)
    with pytest.raises(ManagedConfigError, match="not a directory"):
        store._validate_directory_stat(os.stat(regular_file), path=regular_file, final=True)
    with pytest.raises(ManagedConfigError, match="must not be a symlink"):
        store._ensure_directory(symlink)


def test_managed_io_cleanup_helpers_preserve_primary_failure_context(
    tmp_path: Path, monkeypatch
) -> None:
    store = ManagedConfigStore(tmp_path / "admin")
    directory_fd = store._open_state_directory()
    symlink_target = tmp_path / "target.json"
    symlink_target.write_text("{}", encoding="utf-8")
    symlink_path = store.state_dir / "managed-config.json"
    symlink_path.symlink_to(symlink_target)
    try:
        with pytest.raises(ManagedConfigError, match="Refusing to replace symlink"):
            store._reject_symlink_target(directory_fd, symlink_path)
    finally:
        os.close(directory_fd)

    with pytest.raises(ManagedConfigError, match="outside trusted state"):
        store._trusted_directory_for_write(tmp_path / "outside.json")
    with pytest.raises(OSError, match="first cleanup failure") as caught:
        store._raise_cleanup_errors(
            [OSError("first cleanup failure"), OSError("second cleanup failure")],
            replaced=False,
        )
    assert any(
        "Additional managed-state cleanup failure" in note for note in caught.value.__notes__
    )

    directory_fd = store._open_state_directory()
    file_fd, temporary_name = store._create_temp_file(directory_fd, "managed-config.json")

    def fail_chmod(*_args: object) -> None:
        raise OSError("chmod failure")

    monkeypatch.setattr("chronikwerk.config._managed_io.os.fchmod", fail_chmod)
    with pytest.raises(OSError, match="chmod failure"):
        store._write_temp_payload(file_fd, b"{}")
    os.unlink(temporary_name, dir_fd=directory_fd)
    os.close(directory_fd)
