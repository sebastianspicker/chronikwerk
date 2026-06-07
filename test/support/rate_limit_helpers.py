from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.server import create_app
from zammad_pdf_archiver.config.settings import Settings


def rate_limit_settings(storage_root: str) -> Settings:
    return make_settings(
        storage_root,
        overrides={
            "hardening": {
                "rate_limit": {"enabled": True, "rps": 0, "burst": 2},
                "body_size_limit": {"max_bytes": 1024 * 1024},
            }
        },
    )


async def stub_process_ticket(delivery_id: Any, payload: Any, settings: Any) -> None:  # noqa: ARG001
    return None


def client_with_stubbed_ingest(tmp_path: Any, monkeypatch: Any) -> TestClient:
    app = create_app(rate_limit_settings(str(tmp_path)))
    import zammad_pdf_archiver.app.routes.ingest as ingest_route

    monkeypatch.setattr(ingest_route, "process_ticket", stub_process_ticket)
    return TestClient(app)
