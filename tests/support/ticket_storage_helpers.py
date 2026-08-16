"""Shared fixtures and assertions for ticket-storage tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from chronikwerk.app.jobs.ticket_storage import StoreTicketFilesRequest
from chronikwerk.config.settings import Settings
from chronikwerk.domain.snapshot_models import Article, Snapshot, TicketMeta


@dataclass(frozen=True)
class StorageRequestOverrides:
    """Optional values used by the two non-default storage scenarios."""

    snapshot: Snapshot | None = None
    signing_cert_fingerprint: str | None = None


def storage_settings(tmp_path: Path) -> Settings:
    """Build settings isolated to this ticket-storage test scenario."""
    return Settings.from_mapping(
        {
            "zammad": {
                "base_url": "https://zammad.example.invalid",
                "api_token": "token",
            },
            "storage": {"root": str(tmp_path), "fsync": False},
        }
    )


def storage_snapshot() -> Snapshot:
    """Build a representative ticket snapshot fixture."""
    return Snapshot(
        ticket=TicketMeta(
            id=100,
            number="10001",
            title="Storage failure",
            created_at=datetime(2025, 1, 1, tzinfo=UTC),
        ),
        articles=[
            Article.model_construct(
                id=10,
                created_at=datetime(2025, 1, 1, tzinfo=UTC),
                attachments=[],
            )
        ],
    )


def storage_request(
    *,
    pdf_bytes: bytes,
    paths: tuple[Path, Path],
    settings: Settings,
    overrides: StorageRequestOverrides | None = None,
) -> StoreTicketFilesRequest:
    """Build a ticket-storage request with the shared scenario defaults."""
    target_path, sidecar_path = paths
    resolved = overrides or StorageRequestOverrides()
    return StoreTicketFilesRequest(
        pdf_bytes=pdf_bytes,
        snapshot=resolved.snapshot or storage_snapshot(),
        target_path=target_path,
        sidecar_path=sidecar_path,
        ticket_id=100,
        now=datetime(2025, 1, 1, tzinfo=UTC),
        settings=settings,
        signing_cert_fingerprint=resolved.signing_cert_fingerprint,
    )


def assert_no_partial_artifacts(target: Path, sidecar: Path) -> None:
    """Assert a failed first write leaves no canonical or temporary artifacts."""
    assert not target.exists()
    assert not sidecar.exists()
    assert not list(target.parent.glob(".tmp-archiving-*"))
