from __future__ import annotations

from typing import Any

from scripts.e2e.docker_api_smoke_errors import PROCESSED_STATUS, E2EFailure


def assert_artifacts(
    artifact_payload: dict[str, Any],
    expected_ticket_ids: set[int],
) -> None:
    pdf_count = int(artifact_payload.get("pdf_count", 0))
    bad_pdfs = [str(path) for path in artifact_payload.get("bad_pdfs", [])]
    sidecar_ticket_ids = {int(value) for value in artifact_payload.get("sidecar_ticket_ids", [])}
    missing_sidecars = sorted(expected_ticket_ids - sidecar_ticket_ids)

    if pdf_count < len(expected_ticket_ids):
        raise E2EFailure(
            f"artifact phase: expected at least {len(expected_ticket_ids)} PDFs, got {pdf_count}"
        )
    if bad_pdfs:
        raise E2EFailure("artifact phase: PDFs without %PDF header: " + ", ".join(bad_pdfs))
    if missing_sidecars:
        raise E2EFailure(
            "artifact phase: missing sidecars for ticket IDs "
            + ", ".join(str(ticket_id) for ticket_id in missing_sidecars)
        )


def expected_processed_ticket_ids(expected_statuses: dict[int, str]) -> set[int]:
    return {
        ticket_id for ticket_id, status in expected_statuses.items() if status == PROCESSED_STATUS
    }
