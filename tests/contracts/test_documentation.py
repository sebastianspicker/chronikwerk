"""Verify that required public technical documentation remains present and accurate."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chronikwerk.app.server import create_app
from chronikwerk.config.managed import MANAGED_FIELDS
from tests.support.settings_factory import make_settings


def _enabled_api_routes(application: Any) -> set[tuple[str, str]]:
    """Collect product API routes from the routers included in the live application."""
    routes: set[tuple[str, str]] = set()
    for included in application.routes:
        router = getattr(included, "original_router", None)
        if router is None:
            continue
        for route in router.routes:
            path = route.path
            is_admin_api = path.startswith("/admin/api/")
            if not is_admin_api and not route.include_in_schema:
                continue
            for method in (route.methods or set()) - {"HEAD", "OPTIONS"}:
                routes.add((method, path))
    return routes


def test_documentation_key_docs_exist() -> None:
    """NFR8: Key documentation files exist."""
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
    assert not missing, f"Missing docs: {missing}"


def test_documentation_jobs_history_docs_match_route() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    api = (repo_root / "docs" / "api.md").read_text(encoding="utf-8")
    operations = (repo_root / "docs" / "08-operations.md").read_text(encoding="utf-8")

    assert "## `GET /jobs/history`" in api
    assert "- `GET /jobs/history`" in operations


def test_documentation_api_inventory_matches_enabled_application_routes(tmp_path: Path) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={
            "admin": {
                "enabled": True,
                "access_token": "documentation-contract-admin-token",
                "state_dir": str((tmp_path / "admin-state").resolve()),
            },
            "observability": {
                "history_enabled": True,
                "history_bearer_token": "documentation-contract-history-token",
                "metrics_enabled": True,
                "metrics_bearer_token": "documentation-contract-metrics-token",
            },
            "retry_bearer_token": "documentation-contract-retry-token",
        },
    )
    api = (Path(__file__).resolve().parents[2] / "docs" / "api.md").read_text(encoding="utf-8")

    missing = []
    for method, path in sorted(_enabled_api_routes(create_app(settings))):
        documented = (
            f"| `{method}` | `{path}` |"
            if path.startswith("/admin/api/")
            else f"## `{method} {path}`"
        )
        if documented not in api:
            missing.append(f"{method} {path}")

    assert not missing, f"Undocumented enabled API routes: {missing}"


def test_documentation_configuration_lists_every_managed_field() -> None:
    reference = (Path(__file__).resolve().parents[2] / "docs" / "config-reference.md").read_text(
        encoding="utf-8"
    )

    missing = [field.path for field in MANAGED_FIELDS if f"| `{field.path}` |" not in reference]
    assert not missing, f"Managed configuration fields missing from reference: {missing}"


def test_documentation_matches_explicit_insecure_zammad_override(tmp_path: Path) -> None:
    """Keep the public transport contract aligned with the configured runtime boundary."""
    settings = make_settings(
        str(tmp_path),
        overrides={
            "zammad": {"base_url": "http://zammad.internal"},
            "hardening": {
                "transport": {
                    "allow_insecure_http": True,
                    "allow_private_networks": True,
                }
            },
        },
    )
    assert settings.zammad_connection.origin == "http://zammad.internal"

    repo_root = Path(__file__).resolve().parents[2]
    readme = (repo_root / "README.md").read_text(encoding="utf-8")
    reference = (repo_root / "docs" / "config-reference.md").read_text(encoding="utf-8")
    override = "HARDENING__TRANSPORT__ALLOW_INSECURE_HTTP"

    assert override in readme
    assert "reviewed, isolated internal or test deployment" in readme
    assert override in reference
    assert "reviewed, isolated internal or test deployment" in reference


def test_documentation_indexes_accepted_adr_inventory() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    docs_index = (repo_root / "docs" / "README.md").read_text(encoding="utf-8")
    accepted_adrs = (
        "0004-current-architecture.md",
        "0005-admin-config-and-accessible-pdf.md",
        "0006-zammad-outbound-transport-trust-boundary.md",
        "0007-deterministic-release-assurance-scripts.md",
    )

    for filename in accepted_adrs:
        assert (repo_root / "docs" / "adr" / filename).is_file()
        assert f"(adr/{filename})" in docs_index
