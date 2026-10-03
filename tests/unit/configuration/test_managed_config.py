"""Exercise managed configuration persistence through its public store contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chronikwerk.configuration.errors import ManagedConfigError, RevisionConflict
from chronikwerk.configuration.revisions import ManagedConfigStore


def test_store_stages_lists_and_restores_a_revision_chain(tmp_path: Path) -> None:
    store = ManagedConfigStore(tmp_path / "admin-state", keep_revisions=3)
    initial = store.current_revision()

    first = store.stage(
        {"workflow": {"trigger_tag": "archive:ready"}},
        expected_revision=initial,
        request_id="request-1",
    )
    second = store.stage(
        {"pdf": {"locale": "en-GB"}},
        expected_revision=first["revision"],
        request_id="request-2",
    )

    assert store.load() == {"pdf": {"locale": "en-GB"}}
    assert [item["revision"] for item in store.list_revisions()] == [
        second["revision"],
        first["revision"],
    ]
    assert store.revision_overlay(first["revision"]) == {
        "workflow": {"trigger_tag": "archive:ready"}
    }

    restored = store.restore(
        first["revision"],
        expected_revision=second["revision"],
        request_id="request-3",
    )

    assert restored["previous_revision"] == second["revision"]
    assert store.load() == {"workflow": {"trigger_tag": "archive:ready"}}
    assert not list(store.state_dir.rglob(".*"))
    assert (store.overlay_path.stat().st_mode & 0o777) == 0o600


def test_store_rejects_stale_revisions_and_accepts_legacy_overlay_payload(tmp_path: Path) -> None:
    store = ManagedConfigStore(tmp_path / "admin-state")
    initial = store.current_revision()
    store.overlay_path.write_text(
        json.dumps({"workflow": {"require_tag": False}}),
        encoding="utf-8",
    )

    assert store.load() == {"workflow": {"require_tag": False}}
    assert store.current_revision() != initial
    with pytest.raises(RevisionConflict, match="revision changed"):
        store.stage(
            {"workflow": {"trigger_tag": "archive:ready"}},
            expected_revision=initial,
            request_id="request-stale",
        )


def _stage_tags(store: ManagedConfigStore, count: int) -> list[str]:
    """Stage ``count`` sequential revisions and return their identifiers."""
    revisions: list[str] = []
    expected = store.current_revision()
    for index in range(count):
        metadata = store.stage(
            {"workflow": {"trigger_tag": f"archive:{index}"}},
            expected_revision=expected,
            request_id=f"request-{index}",
        )
        expected = metadata["revision"]
        revisions.append(expected)
    return revisions


def test_store_creates_private_state_directories_and_files(tmp_path: Path) -> None:
    store = ManagedConfigStore(tmp_path / "admin-state")
    revision = _stage_tags(store, 1)[0]

    assert (store.state_dir.stat().st_mode & 0o777) == 0o700
    assert (store.revisions_dir.stat().st_mode & 0o777) == 0o700
    assert ((store.revisions_dir / f"{revision}.json").stat().st_mode & 0o777) == 0o600
    assert (store.overlay_path.stat().st_mode & 0o777) == 0o600


def test_store_rejects_a_symlinked_state_directory(tmp_path: Path) -> None:
    real = tmp_path / "real-state"
    real.mkdir(mode=0o700)
    (tmp_path / "linked-state").symlink_to(real, target_is_directory=True)

    with pytest.raises(ManagedConfigError, match="symlink or non-directory"):
        ManagedConfigStore(tmp_path / "linked-state")


def test_store_rejects_a_symlinked_current_file(tmp_path: Path) -> None:
    store = ManagedConfigStore(tmp_path / "admin-state")
    target = tmp_path / "outside.json"
    target.write_text("{}", encoding="utf-8")
    store.overlay_path.symlink_to(target)

    with pytest.raises(ManagedConfigError, match="not found or unsafe"):
        store.load()


def test_store_rejects_a_symlinked_revision_file(tmp_path: Path) -> None:
    store = ManagedConfigStore(tmp_path / "admin-state")
    revision = _stage_tags(store, 1)[0]
    revision_path = store.revisions_dir / f"{revision}.json"
    target = tmp_path / "outside.json"
    target.write_text(revision_path.read_text(encoding="utf-8"), encoding="utf-8")
    revision_path.unlink()
    revision_path.symlink_to(target)

    with pytest.raises(ManagedConfigError, match="not found or unsafe"):
        store.revision_overlay(revision)


def test_store_rejects_an_oversized_current_file(tmp_path: Path) -> None:
    store = ManagedConfigStore(tmp_path / "admin-state")
    store.overlay_path.write_text(" " * (256 * 1024 + 1), encoding="utf-8")

    with pytest.raises(ManagedConfigError, match="exceeds 256 KiB"):
        store.load()


def test_store_rejects_an_oversized_payload_before_writing(tmp_path: Path) -> None:
    store = ManagedConfigStore(tmp_path / "admin-state")
    initial = store.current_revision()

    with pytest.raises(ManagedConfigError, match="exceeds 256 KiB"):
        store.stage(
            {"signing": {"pades": {"reason": "x" * (256 * 1024)}}},
            expected_revision=initial,
            request_id="request-big",
        )

    assert store.current_revision() == initial
    assert not list(store.revisions_dir.iterdir())


def test_store_prunes_revision_files_to_the_retention_limit(tmp_path: Path) -> None:
    store = ManagedConfigStore(tmp_path / "admin-state", keep_revisions=3)
    revisions = _stage_tags(store, 5)

    kept = {path.stem for path in store.revisions_dir.glob("*.json")}
    assert kept == set(revisions[-3:])
