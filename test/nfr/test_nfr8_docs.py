"""NFR8: Document Zammad setup, path policy, signing, storage, operations, security."""

from __future__ import annotations

from pathlib import Path


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
        "PRD.md",
    ]
    missing = [f for f in required if not (docs / f).is_file()]
    assert not missing, f"Missing docs: {missing}"


def test_nfr8_jobs_auth_docs_match_routes() -> None:
    repo_root = Path(__file__).resolve().parents[2]

    readme = (repo_root / "README.md").read_text(encoding="utf-8")
    api = (repo_root / "docs" / "api.md").read_text(encoding="utf-8")
    operations = (repo_root / "docs" / "08-operations.md").read_text(encoding="utf-8")
    security = (repo_root / "docs" / "09-security.md").read_text(encoding="utf-8")

    assert "`GET /jobs/{ticket_id}` (requires Bearer token via `ADMIN_BEARER_TOKEN`)" in readme
    assert "`GET /jobs/queue/stats` (requires Bearer token via `ADMIN_BEARER_TOKEN`)" in readme
    assert "### `GET /jobs/{ticket_id}`" in api
    assert "### `GET /jobs/queue/stats`" in api
    assert api.count("Requires `Authorization: Bearer <ADMIN_BEARER_TOKEN>`.") >= 4
    assert "- `GET /jobs/{ticket_id}`" in operations
    assert "- `GET /jobs/queue/stats`" in operations
    assert operations.count("requires `Authorization: Bearer <ADMIN_BEARER_TOKEN>`") >= 4
    assert "/jobs/{ticket_id}" in security
    assert "/jobs/queue/stats" in security
