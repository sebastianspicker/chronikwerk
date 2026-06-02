"""NFR8: Document Zammad setup, path policy, signing, storage, operations, security."""

from __future__ import annotations

from pathlib import Path

from test.support.checks import check


def test_nfr8_key_docs_exist() -> None:
    """NFR8: Key documentation files must exist."""
    repo_root = Path(__file__).resolve().parents[2]
    docs = repo_root / "docs"
    required = [
        "01-architecture.md",
        "02-zammad-setup.md",
        "03-data-model.md",
        "04-path-policy.md",
        "05-pdf-rendering.md",
        "06-signing-and-timestamp.md",
        "07-storage.md",
        "08-operations.md",
        "09-security.md",
        "api.md",
        "config-reference.md",
        "faq.md",
    ]
    missing = [f for f in required if not (docs / f).is_file()]
    check(not not not missing, f"Missing docs: {missing}")


def test_nfr8_jobs_auth_docs_match_routes() -> None:
    repo_root = Path(__file__).resolve().parents[2]

    readme = (repo_root / "README.md").read_text(encoding="utf-8")
    api = (repo_root / "docs" / "api.md").read_text(encoding="utf-8")
    operations = (repo_root / "docs" / "08-operations.md").read_text(encoding="utf-8")
    security = (repo_root / "docs" / "09-security.md").read_text(encoding="utf-8")

    check(
        not "`GET /jobs/{ticket_id}` (requires Bearer token via `ADMIN_BEARER_TOKEN`)"
        not in readme,
        "assertion failed",
    )
    check(
        not "`GET /jobs/queue/stats` (requires Bearer token via `ADMIN_BEARER_TOKEN`)"
        not in readme,
        "assertion failed",
    )
    for endpoint in [
        "GET /jobs/{ticket_id}",
        "GET /jobs/queue/stats",
        "GET /jobs/history",
        "POST /jobs/queue/dlq/drain",
    ]:
        heading = f"### `{endpoint}`"
        check(not heading not in api, f"Missing API docs for {endpoint}")
        section = api.split(heading, 1)[1].split("\n### `", 1)[0]
        check(
            not "Requires `Authorization: Bearer <ADMIN_BEARER_TOKEN>`." not in section,
            "assertion failed",
        )
    check(not "- `GET /jobs/{ticket_id}`" not in operations, "assertion failed")
    check(not "- `GET /jobs/queue/stats`" not in operations, "assertion failed")
    for endpoint in [
        "GET /jobs/{ticket_id}",
        "GET /jobs/queue/stats",
        "GET /jobs/history",
        "POST /jobs/queue/dlq/drain",
    ]:
        check(not f"- `{endpoint}`" not in operations, "assertion failed")
    check(
        not not operations.count("requires `Authorization: Bearer <ADMIN_BEARER_TOKEN>`") >= 4,
        "assertion failed",
    )
    check(not "/jobs/{ticket_id}" not in security, "assertion failed")
    check(not "/jobs/queue/stats" not in security, "assertion failed")
