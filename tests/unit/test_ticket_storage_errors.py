"""Verifies ticket-storage failures roll back artifacts and protect signer metadata."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from chronikwerk.app.jobs import ticket_storage
from chronikwerk.config.settings import Settings
from tests.support.ticket_storage_helpers import (
    StorageRequestOverrides,
    assert_no_partial_artifacts,
    storage_request,
    storage_settings,
)


def test_store_ticket_files_sidecar_failure_removes_moved_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = storage_settings(tmp_path)
    target = tmp_path / "archive" / "Ticket-10001.pdf"
    sidecar = target.with_suffix(".pdf.json")
    original_move = ticket_storage.move_file_within_root

    def fail_sidecar_move(src: Path, dst: Path, *_args: Any, **_kwargs: Any) -> None:
        if dst == sidecar:
            raise OSError("sidecar move failed")
        original_move(src, dst, *_args, **_kwargs)

    monkeypatch.setattr(ticket_storage, "move_file_within_root", fail_sidecar_move)

    with pytest.raises(OSError, match="sidecar move failed"):
        ticket_storage.store_ticket_files_request(
            storage_request(
                pdf_bytes=b"%PDF",
                paths=(target, sidecar),
                settings=settings,
            )
        )

    assert_no_partial_artifacts(target, sidecar)


def test_store_ticket_files_rejects_outside_target_before_creating_temp_directory(
    tmp_path: Path,
) -> None:
    storage_root = tmp_path / "root"
    settings = storage_settings(storage_root)
    target = tmp_path / "outside" / "Ticket-10001.pdf"
    sidecar = target.with_suffix(".pdf.json")

    with pytest.raises(ValueError, match="escapes root"):
        ticket_storage.store_ticket_files_request(
            storage_request(
                pdf_bytes=b"%PDF",
                paths=(target, sidecar),
                settings=settings,
            )
        )

    assert not target.parent.exists()


def test_store_ticket_files_uses_rendered_signer_fingerprint_after_pfx_swap(
    tmp_path: Path,
) -> None:
    settings = Settings.from_mapping(
        {
            "zammad": {"base_url": "https://zammad.example.invalid", "api_token": "token"},
            "storage": {"root": str(tmp_path), "fsync": False},
            "signing": {"enabled": True, "pfx_path": str(tmp_path / "rotated.pfx")},
        }
    )
    target = tmp_path / "archive" / "Ticket-10001.pdf"
    sidecar = target.with_suffix(".pdf.json")

    # This represents PFX material being replaced after rendering/signing but
    # before the audit sidecar is written.
    (tmp_path / "rotated.pfx").write_bytes(b"replacement material")
    ticket_storage.store_ticket_files_request(
        storage_request(
            pdf_bytes=b"%PDF",
            paths=(target, sidecar),
            settings=settings,
            overrides=StorageRequestOverrides(signing_cert_fingerprint="fingerprint-of-signed-pdf"),
        )
    )

    audit = json.loads(sidecar.read_text("utf-8"))
    assert audit["signing"]["cert_fingerprint"] == "fingerprint-of-signed-pdf"


def test_store_ticket_files_legacy_signature_writes_durable_archive(tmp_path: Path) -> None:
    settings = storage_settings(tmp_path)
    target = tmp_path / "archive" / "Ticket-10001.pdf"
    sidecar = target.with_suffix(".pdf.json")
    pdf_bytes = b"%PDF legacy public API"
    request = storage_request(
        pdf_bytes=pdf_bytes,
        paths=(target, sidecar),
        settings=settings,
    )

    result = ticket_storage.store_ticket_files(
        pdf_bytes=request.pdf_bytes,
        snapshot=request.snapshot,
        target_path=request.target_path,
        sidecar_path=request.sidecar_path,
        ticket_id=request.ticket_id,
        now=request.now,
        settings=request.settings,
        signing_cert_fingerprint=request.signing_cert_fingerprint,
    )

    assert result == ticket_storage.StorageResult(
        target_path=target,
        sidecar_path=sidecar,
        sha256_hex="66611ca92c9cc8820600a3f38d9458f62f87253ce4beec069a3c8a15f0e87837",
        size_bytes=len(pdf_bytes),
    )
    assert target.read_bytes() == pdf_bytes
    assert json.loads(sidecar.read_text("utf-8"))["ticket_id"] == request.ticket_id


def test_store_ticket_files_legacy_signature_delegates_to_request_entry_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = storage_request(
        pdf_bytes=b"%PDF legacy delegation",
        paths=(
            tmp_path / "archive" / "Ticket-10001.pdf",
            tmp_path / "archive" / "Ticket-10001.pdf.json",
        ),
        settings=storage_settings(tmp_path),
    )
    expected = ticket_storage.StorageResult(
        target_path=request.target_path,
        sidecar_path=request.sidecar_path,
        sha256_hex="0" * 64,
        size_bytes=0,
    )
    captured: list[ticket_storage.StoreTicketFilesRequest] = []

    def record_request(
        request_entry: ticket_storage.StoreTicketFilesRequest,
    ) -> ticket_storage.StorageResult:
        captured.append(request_entry)
        return expected

    monkeypatch.setattr(ticket_storage, "store_ticket_files_request", record_request)

    assert (
        ticket_storage.store_ticket_files(
            pdf_bytes=request.pdf_bytes,
            snapshot=request.snapshot,
            target_path=request.target_path,
            sidecar_path=request.sidecar_path,
            ticket_id=request.ticket_id,
            now=request.now,
            settings=request.settings,
            signing_cert_fingerprint=request.signing_cert_fingerprint,
        )
        == expected
    )
    assert captured == [request]
