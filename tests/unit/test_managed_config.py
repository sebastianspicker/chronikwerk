"""Verifies managed configuration staging, validation, atomicity, and filesystem hardening."""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from chronikwerk.config.managed import (
    ManagedConfigError,
    ManagedConfigStore,
    RevisionConflict,
    config_read_model,
    overlay_from_flat,
    revision_for,
    secret_presence,
    validate_candidate,
)
from tests.support.settings_factory import make_settings


def _assert_revision_state(
    store: ManagedConfigStore,
    *,
    current_revision: str | None = None,
    overlay: dict[str, object] | None = None,
    revision_count: int | None = None,
) -> None:
    """Assert the selected durable state facets shared by fault scenarios."""
    if current_revision is not None:
        assert store.current_revision() == current_revision
    if overlay is not None:
        assert store.load() == overlay
    if revision_count is not None:
        assert len(list(store.revisions_dir.glob("*.json"))) == revision_count


def _assert_revision_bytes_unchanged(
    store: ManagedConfigStore,
    *,
    current_revision: str,
    overlay: dict[str, object],
    current_bytes: bytes,
    revision_bytes: dict[str, bytes],
) -> None:
    """Assert a rejected stage left both pointers and revision files unchanged."""
    _assert_revision_state(store, current_revision=current_revision, overlay=overlay)
    assert store.overlay_path.read_bytes() == current_bytes
    assert {
        path.name: path.read_bytes() for path in store.revisions_dir.glob("*.json")
    } == revision_bytes


def _assert_retained_revisions(
    store: ManagedConfigStore,
    expected_revisions: list[str],
    removed_revision: str,
) -> None:
    """Assert retention keeps the newest chain and removes the cutoff entry."""
    assert [item["revision"] for item in store.list_revisions()] == expected_revisions
    assert not (store.revisions_dir / f"{removed_revision}.json").exists()


def _stage_two_revisions(store: ManagedConfigStore) -> None:
    """Create the retention scenario with one current and one old revision."""
    first = store.stage(
        {"pdf": {"max_articles": 100}},
        expected_revision=store.current_revision(),
        request_id="first",
    )
    store.stage(
        {"pdf": {"max_articles": 50}},
        expected_revision=first["revision"],
        request_id="second",
    )


def test_store_stages_atomically_and_enforces_revision_precondition(tmp_path: Path) -> None:
    store = ManagedConfigStore(tmp_path / "admin")
    initial = store.current_revision()

    metadata = store.stage(
        {"pdf": {"max_articles": 100}},
        expected_revision=initial,
        request_id="request-1",
    )

    assert store.load() == {"pdf": {"max_articles": 100}}
    assert metadata["previous_revision"] == initial
    assert metadata["changed_paths"] == ["pdf.max_articles"]
    assert store.overlay_path.stat().st_mode & 0o777 == 0o600
    assert store.list_revisions()[0]["request_id"] == "request-1"
    with pytest.raises(RevisionConflict):
        store.stage({}, expected_revision=initial, request_id="stale")


def test_store_reads_legacy_unwrapped_overlay(tmp_path: Path) -> None:
    store = ManagedConfigStore(tmp_path / "admin")
    overlay = {"pdf": {"max_articles": 42}}
    store.overlay_path.write_text(json.dumps(overlay), encoding="utf-8")
    store.overlay_path.chmod(0o600)

    assert store.load() == overlay
    assert store.current_revision() == revision_for(overlay)


def test_store_rejects_secrets_unknown_fields_and_symlink_state(tmp_path: Path) -> None:
    with pytest.raises(ManagedConfigError, match="Secret field"):
        overlay_from_flat({"zammad.api_token": "nope"})
    with pytest.raises(ManagedConfigError, match="Unknown"):
        overlay_from_flat({"server.port": 9999})

    target = tmp_path / "target"
    target.mkdir()
    symlink = tmp_path / "admin"
    symlink.symlink_to(target, target_is_directory=True)
    with pytest.raises(ManagedConfigError, match="symlink"):
        ManagedConfigStore(symlink)


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory permissions required")
def test_store_rejects_group_or_world_writable_state_directory(tmp_path: Path) -> None:
    state_dir = tmp_path / "admin"
    state_dir.mkdir()
    state_dir.chmod(0o777)

    with pytest.raises(ManagedConfigError, match="group or world writable"):
        ManagedConfigStore(state_dir)


@pytest.mark.skipif(os.name != "posix", reason="POSIX no-follow directory opens required")
def test_store_rejects_intermediate_symlink_without_creating_state(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(target, target_is_directory=True)

    with pytest.raises(ManagedConfigError, match="symlink"):
        ManagedConfigStore(alias / "admin")

    assert not (target / "admin").exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory identity checks required")
def test_store_rejects_ancestor_substitution_after_initialization(tmp_path: Path) -> None:
    ancestor = tmp_path / "state-root"
    store = ManagedConfigStore(ancestor / "admin")
    displaced = tmp_path / "state-root-original"
    ancestor.rename(displaced)
    (ancestor / "admin" / "revisions").mkdir(parents=True)

    with pytest.raises(ManagedConfigError, match="changed after initialization"):
        store.load()

    assert not (ancestor / "admin" / "managed-config.json").exists()


def test_candidate_validation_normalizes_locale_and_never_exposes_secret(tmp_path: Path) -> None:
    settings = make_settings(
        str(tmp_path),
        secret="test-webhook-hmac-secret-0123456789abcdef",
    )
    _candidate, normalized = validate_candidate(
        settings,
        {"pdf": {"locale": "en_GB", "max_articles": 12}},
    )

    assert normalized == {"pdf": {"locale": "en-GB", "max_articles": 12}}
    presence = secret_presence(settings)
    assert presence["zammad.api_token"] is True
    assert all(isinstance(value, bool) for value in presence.values())


def test_read_model_shows_staged_value_before_external_restart(tmp_path: Path) -> None:
    """Keep a staged value visible so a later edit cannot silently replace it."""
    settings = make_settings(
        str(tmp_path),
        secret="test-webhook-hmac-secret-0123456789abcdef",
    )

    fields = config_read_model(settings, {"pdf": {"max_articles": 249}})
    max_articles = next(field for field in fields if field["path"] == "pdf.max_articles")

    assert max_articles["value"] == 249
    assert max_articles["source"] == "managed"


def test_failed_atomic_replace_preserves_current_overlay(tmp_path: Path, monkeypatch) -> None:
    store = ManagedConfigStore(tmp_path / "admin")
    first = store.stage(
        {"pdf": {"max_articles": 100}},
        expected_revision=store.current_revision(),
        request_id="first",
    )

    def fail_replace(_source: str, _target: str, **_kwargs: object) -> None:
        raise OSError("fault injected")

    monkeypatch.setattr("chronikwerk.config.managed.os.replace", fail_replace)
    with pytest.raises(OSError, match="fault injected"):
        store.stage(
            {"pdf": {"max_articles": 50}},
            expected_revision=first["revision"],
            request_id="second",
        )

    assert store.load() == {"pdf": {"max_articles": 100}}


def test_failed_current_pointer_write_removes_orphan_revision(tmp_path: Path, monkeypatch) -> None:
    store = ManagedConfigStore(tmp_path / "admin")
    first = store.stage(
        {"pdf": {"max_articles": 100}},
        expected_revision=store.current_revision(),
        request_id="first",
    )
    original_write = store._atomic_write
    calls = 0

    def fail_second_write(path: Path, value: dict[str, object]) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("current pointer fault injected")
        original_write(path, value)

    monkeypatch.setattr(store, "_atomic_write", fail_second_write)
    with pytest.raises(OSError, match="current pointer fault injected"):
        store.stage(
            {"pdf": {"max_articles": 50}},
            expected_revision=first["revision"],
            request_id="second",
        )

    _assert_revision_state(
        store,
        current_revision=first["revision"],
        revision_count=1,
    )


@pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor cleanup required")
def test_atomic_write_preserves_primary_cleanup_error_and_closes_directory(
    tmp_path: Path, monkeypatch
) -> None:
    store = ManagedConfigStore(tmp_path / "admin")
    opened_directory_fds: list[int] = []
    original_open_state = store._open_state_directory

    def tracked_open_state() -> int:
        directory_fd = original_open_state()
        opened_directory_fds.append(directory_fd)
        return directory_fd

    def fail_replace(*_args: object, **_kwargs: object) -> None:
        raise OSError("primary replace failure")

    def fail_unlink(*_args: object, **_kwargs: object) -> None:
        raise OSError("secondary unlink failure")

    monkeypatch.setattr(store, "_open_state_directory", tracked_open_state)
    monkeypatch.setattr("chronikwerk.config.managed.os.replace", fail_replace)
    monkeypatch.setattr("chronikwerk.config.managed.os.unlink", fail_unlink)

    with pytest.raises(OSError, match="primary replace failure") as caught:
        store._atomic_write(store.overlay_path, {"revision": "0" * 64, "overlay": {}})

    assert any("secondary unlink failure" in note for note in caught.value.__notes__)
    assert opened_directory_fds
    for directory_fd in opened_directory_fds:
        with pytest.raises(OSError):
            os.fstat(directory_fd)


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory fsync required")
def test_post_replace_fsync_failure_keeps_visible_revision_consistent(
    tmp_path: Path, monkeypatch
) -> None:
    store = ManagedConfigStore(tmp_path / "admin")
    original_fsync = os.fsync
    calls = 0

    def fail_current_directory_fsync(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 4:
            raise OSError("current directory fsync failure")
        original_fsync(fd)

    monkeypatch.setattr("chronikwerk.config.managed.os.fsync", fail_current_directory_fsync)

    with pytest.raises(ManagedConfigError, match="replaced but directory fsync failed"):
        store.stage(
            {"pdf": {"max_articles": 77}},
            expected_revision=store.current_revision(),
            request_id="fsync-fault",
        )

    _assert_revision_state(store, overlay={"pdf": {"max_articles": 77}}, revision_count=1)


@pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor cleanup required")
def test_post_commit_close_failure_keeps_revision_history_consistent(
    tmp_path: Path, monkeypatch
) -> None:
    store = ManagedConfigStore(tmp_path / "admin")
    state_identity = store._state_identity
    assert state_identity is not None
    original_atomic_write = store._atomic_write
    original_close = os.close
    atomic_write_calls = 0
    fail_state_close = False

    def tracked_atomic_write(path: Path, value: dict[str, object]) -> None:
        nonlocal atomic_write_calls, fail_state_close
        atomic_write_calls += 1
        if atomic_write_calls == 2:
            fail_state_close = True
        original_atomic_write(path, value)

    def fail_committed_state_close(fd: int) -> None:
        nonlocal fail_state_close
        fd_stat = os.fstat(fd)
        if fail_state_close and (fd_stat.st_dev, fd_stat.st_ino) == state_identity:
            fail_state_close = False
            original_close(fd)
            raise OSError("injected post-commit close failure")
        original_close(fd)

    monkeypatch.setattr(store, "_atomic_write", tracked_atomic_write)
    monkeypatch.setattr("chronikwerk.config.managed.os.close", fail_committed_state_close)

    with pytest.raises(ManagedConfigError, match="committed but descriptor cleanup failed"):
        store.stage(
            {"pdf": {"max_articles": 77}},
            expected_revision=store.current_revision(),
            request_id="close-fault",
        )

    assert store.load() == {"pdf": {"max_articles": 77}}
    assert [item["request_id"] for item in store.list_revisions()] == ["close-fault"]


def test_prune_failure_after_commit_does_not_report_stage_failure(
    tmp_path: Path, monkeypatch
) -> None:
    store = ManagedConfigStore(tmp_path / "admin")

    def fail_prune() -> None:
        raise OSError("retention cleanup failure")

    monkeypatch.setattr(store, "_prune_revisions", fail_prune)
    metadata = store.stage(
        {"pdf": {"max_articles": 88}},
        expected_revision=store.current_revision(),
        request_id="prune-fault",
    )

    assert metadata["request_id"] == "prune-fault"
    assert store.load() == {"pdf": {"max_articles": 88}}


@pytest.mark.skipif(os.name != "posix", reason="POSIX no-follow file opens required")
def test_prune_aborts_without_deleting_on_revision_io_error(tmp_path: Path, monkeypatch) -> None:
    store = ManagedConfigStore(tmp_path / "admin")
    _stage_two_revisions(store)
    store.keep_revisions = 1
    revision_names = {path.name for path in store.revisions_dir.glob("*.json")}
    current_name = f"{store.current_revision()}.json"
    original_open = os.open

    def fail_revision_open(path, *args, **kwargs):
        if path == current_name:
            raise OSError(errno.EIO, "injected revision read failure")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr("chronikwerk.config.managed.os.open", fail_revision_open)

    with pytest.raises(ManagedConfigError, match="not found or unsafe"):
        store._prune_revisions()

    assert {path.name for path in store.revisions_dir.glob("*.json")} == revision_names


@pytest.mark.skipif(os.name != "posix", reason="POSIX no-follow file opens required")
def test_prune_aborts_without_deleting_on_unsafe_current_revision(tmp_path: Path) -> None:
    store = ManagedConfigStore(tmp_path / "admin")
    _stage_two_revisions(store)
    store.keep_revisions = 1
    current_path = store.revisions_dir / f"{store.current_revision()}.json"
    displaced_path = current_path.with_suffix(".backup")
    current_path.rename(displaced_path)
    current_path.symlink_to(displaced_path.name)
    revision_names = {path.name for path in store.revisions_dir.iterdir()}

    with pytest.raises(ManagedConfigError, match="not found or unsafe"):
        store._prune_revisions()

    assert {path.name for path in store.revisions_dir.iterdir()} == revision_names


def test_oversized_stage_is_rejected_before_any_state_change(tmp_path: Path) -> None:
    store = ManagedConfigStore(tmp_path / "admin")
    first = store.stage(
        {"pdf": {"max_articles": 100}},
        expected_revision=store.current_revision(),
        request_id="first",
    )
    current_bytes = store.overlay_path.read_bytes()
    revision_bytes = {path.name: path.read_bytes() for path in store.revisions_dir.glob("*.json")}

    # The compact inbound object remains below 256 KiB, while the durable
    # revision envelope and pretty-printed representation exceed that bound.
    oversized_overlay = {"workflow": {"trigger_tag": "x" * 262_000}}
    assert len(json.dumps(oversized_overlay, separators=(",", ":")).encode()) < 256 * 1024
    with pytest.raises(ManagedConfigError, match="exceeds 256 KiB"):
        store.stage(
            oversized_overlay,
            expected_revision=first["revision"],
            request_id="oversized",
        )

    _assert_revision_bytes_unchanged(
        store,
        current_revision=first["revision"],
        overlay={"pdf": {"max_articles": 100}},
        current_bytes=current_bytes,
        revision_bytes=revision_bytes,
    )


def test_restore_of_same_overlay_creates_new_revision(tmp_path: Path) -> None:
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

    restored = store.restore(
        first["revision"],
        expected_revision=second["revision"],
        request_id="restore",
    )

    assert restored["revision"] not in {first["revision"], second["revision"]}
    assert store.load() == {"pdf": {"max_articles": 100}}
    assert [item["revision"] for item in store.list_revisions()] == [
        restored["revision"],
        second["revision"],
        first["revision"],
    ]


def test_revision_filename_must_match_embedded_metadata(tmp_path: Path) -> None:
    store = ManagedConfigStore(tmp_path / "admin")
    staged = store.stage(
        {"pdf": {"max_articles": 100}},
        expected_revision=store.current_revision(),
        request_id="first",
    )
    replacement_revision = "f" * 64
    assert replacement_revision != staged["revision"]
    source = store.revisions_dir / f"{staged['revision']}.json"
    source.rename(store.revisions_dir / f"{replacement_revision}.json")

    with pytest.raises(ManagedConfigError, match="Invalid revision metadata"):
        store.revision_overlay(replacement_revision)


def test_revision_retention_keeps_only_newest_chain_entries(tmp_path: Path) -> None:
    store = ManagedConfigStore(tmp_path / "admin", keep_revisions=2)
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
    third = store.stage(
        {"pdf": {"max_articles": 25}},
        expected_revision=second["revision"],
        request_id="third",
    )

    _assert_retained_revisions(
        store,
        [third["revision"], second["revision"]],
        first["revision"],
    )


def test_revision_chain_missing_before_retention_cutoff_fails_closed(tmp_path: Path) -> None:
    store = ManagedConfigStore(tmp_path / "admin", keep_revisions=3)
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
    (store.revisions_dir / f"{first['revision']}.json").unlink()

    with pytest.raises(ManagedConfigError, match="Revision chain references missing revision"):
        store.list_revisions()

    assert store.current_revision() == second["revision"]


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
