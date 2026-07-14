from __future__ import annotations

import json
from pathlib import Path

import pytest

from test.support.settings_factory import make_settings
from zammad_pdf_archiver.config.managed import (
    ManagedConfigError,
    ManagedConfigStore,
    RevisionConflict,
    overlay_from_flat,
    secret_presence,
    validate_candidate,
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


def test_failed_atomic_replace_preserves_current_overlay(tmp_path: Path, monkeypatch) -> None:
    store = ManagedConfigStore(tmp_path / "admin")
    first = store.stage(
        {"pdf": {"max_articles": 100}},
        expected_revision=store.current_revision(),
        request_id="first",
    )

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("fault injected")

    monkeypatch.setattr("zammad_pdf_archiver.config.managed.os.replace", fail_replace)
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
