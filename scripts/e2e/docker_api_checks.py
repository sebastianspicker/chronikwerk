"""Assert the observable contracts of the self-contained Docker API E2E lane."""

from __future__ import annotations

from typing import Any

PROCESSED_STATUS = "processed"


class E2EFailure(RuntimeError):
    """Raised when the Docker API E2E lane cannot prove the expected behavior."""


def expected_statuses_from_dataset(dataset: dict[str, Any]) -> dict[int, str]:
    """Normalize the declarative seed plan into the terminal-status oracle."""
    seed_plan = dataset.get("seed_plan")
    if not isinstance(seed_plan, list):
        raise E2EFailure("dataset phase: dataset.seed_plan must be a list")

    expected: dict[int, str] = {}
    for index, item in enumerate(seed_plan):
        if not isinstance(item, dict):
            raise E2EFailure(f"dataset phase: seed_plan[{index}] must be an object")
        try:
            ticket_id = int(item["ticket_id"])
            status = str(item["expected_status"]).strip()
        except (KeyError, TypeError, ValueError) as exc:
            raise E2EFailure(
                f"dataset phase: seed_plan[{index}] must define ticket_id and expected_status"
            ) from exc
        if not status:
            raise E2EFailure(f"dataset phase: seed_plan[{index}].expected_status is empty")
        expected[ticket_id] = status
    return expected


def latest_status_by_ticket(history_payload: dict[str, Any]) -> dict[int, str]:
    """Keep the first history entry per ticket because history is newest-first."""
    items = history_payload.get("entries")
    if not isinstance(items, list):
        raise E2EFailure("history phase: history payload has no entries list")

    statuses: dict[int, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_ticket_id = item.get("ticket_id")
        raw_status = item.get("status")
        if raw_ticket_id is None or raw_status is None:
            continue
        try:
            ticket_id = int(raw_ticket_id)
        except TypeError, ValueError:
            continue
        statuses.setdefault(ticket_id, str(raw_status))
    return statuses


def assert_expected_statuses(
    history_payload: dict[str, Any],
    expected: dict[int, str],
) -> None:
    """Fail with every terminal-status mismatch instead of masking later failures."""
    latest = latest_status_by_ticket(history_payload)
    mismatches = [
        f"ticket {ticket_id}: expected {expected_status!r}, got {latest.get(ticket_id)!r}"
        for ticket_id, expected_status in sorted(expected.items())
        if latest.get(ticket_id) != expected_status
    ]
    if mismatches:
        raise E2EFailure("history phase: terminal status mismatch: " + "; ".join(mismatches))


def assert_artifacts(
    artifact_payload: dict[str, Any],
    expected_ticket_ids: set[int],
) -> None:
    """Verify that every processed ticket has a readable PDF and matching sidecar."""
    artifacts = artifact_payload.get("artifacts")
    if not isinstance(artifacts, list):
        raise E2EFailure("artifact phase: artifact inspection has no artifacts list")

    by_ticket = _artifacts_by_ticket(artifacts)
    pdf_count = int(artifact_payload.get("pdf_count", 0))
    if pdf_count < len(expected_ticket_ids):
        raise E2EFailure(
            f"artifact phase: expected at least {len(expected_ticket_ids)} PDFs, got {pdf_count}"
        )

    bad_pdfs = [str(path) for path in artifact_payload.get("bad_pdfs", [])]
    if bad_pdfs:
        raise E2EFailure("artifact phase: PDFs without %PDF header: " + ", ".join(bad_pdfs))

    missing_sidecars = sorted(expected_ticket_ids - by_ticket.keys())
    if missing_sidecars:
        raise E2EFailure(
            "artifact phase: missing sidecars for ticket IDs "
            + ", ".join(str(ticket_id) for ticket_id in missing_sidecars)
        )

    for ticket_id in sorted(expected_ticket_ids):
        item = by_ticket[ticket_id]
        if item.get("pdf_sha256") != item.get("sha256"):
            raise E2EFailure(f"artifact phase: checksum mismatch for ticket {ticket_id}")


def _artifacts_by_ticket(artifacts: list[object]) -> dict[int, dict[str, Any]]:
    """Index sidecars by ticket while rejecting malformed inspection output early."""
    by_ticket: dict[int, dict[str, Any]] = {}
    for item in artifacts:
        if not isinstance(item, dict):
            raise E2EFailure("artifact phase: malformed artifact entry")
        try:
            by_ticket[int(item["ticket_id"])] = item
        except (KeyError, TypeError, ValueError) as exc:
            raise E2EFailure("artifact phase: sidecar has invalid ticket_id") from exc
    return by_ticket


def expected_processed_ticket_ids(expected_statuses: dict[int, str]) -> set[int]:
    """Restrict artifact expectations to statuses that should emit a PDF."""
    return {
        ticket_id for ticket_id, status in expected_statuses.items() if status == PROCESSED_STATUS
    }


def assert_mock_state(payload: dict[str, Any], expected_processed: set[int]) -> None:
    """Confirm the mock observed signing and archive-note side effects."""
    tags = payload.get("tags")
    notes = payload.get("notes")
    if not isinstance(tags, dict) or not isinstance(notes, dict):
        raise E2EFailure("mock verification phase: state has no tags/notes maps")
    for ticket_id in sorted(expected_processed):
        _assert_ticket_state(ticket_id, tags=tags, notes=notes)


def _assert_ticket_state(
    ticket_id: int,
    *,
    tags: dict[object, object],
    notes: dict[object, object],
) -> None:
    """Check one processed ticket's tag and note state without assuming JSON key types."""
    raw_tags = tags.get(str(ticket_id), tags.get(ticket_id, []))
    if not isinstance(raw_tags, list):
        raise E2EFailure(f"mock verification phase: ticket {ticket_id} has malformed tags")
    if "pdf:signed" not in {str(value) for value in raw_tags}:
        raise E2EFailure(f"mock verification phase: ticket {ticket_id} is not signed")

    ticket_notes = notes.get(str(ticket_id), notes.get(ticket_id, []))
    if not isinstance(ticket_notes, list) or not any(
        isinstance(note, dict) and "PDF archived" in str(note.get("subject", ""))
        for note in ticket_notes
    ):
        raise E2EFailure(f"mock verification phase: ticket {ticket_id} has no archive note")
