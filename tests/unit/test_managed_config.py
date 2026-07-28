"""Verifies managed configuration staging, validation, atomicity, and filesystem hardening."""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path

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

    assert store.current_revision() == first["revision"]
    assert [item["revision"] for item in store.list_revisions()] == [first["revision"]]
    assert len(list(store.revisions_dir.glob("*.json"))) == 1


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

    assert store.load() == {"pdf": {"max_articles": 77}}
    assert len(list(store.revisions_dir.glob("*.json"))) == 1


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

    assert store.current_revision() == first["revision"]
    assert store.load() == {"pdf": {"max_articles": 100}}
    assert store.overlay_path.read_bytes() == current_bytes
    assert {
        path.name: path.read_bytes() for path in store.revisions_dir.glob("*.json")
    } == revision_bytes


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

    assert [item["revision"] for item in store.list_revisions()] == [
        third["revision"],
        second["revision"],
    ]
    assert not (store.revisions_dir / f"{first['revision']}.json").exists()


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
