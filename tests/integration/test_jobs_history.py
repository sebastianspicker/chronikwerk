"""Verifies optional job-history exposure and its fail-closed authorization defaults."""

from __future__ import annotations

from fastapi.testclient import TestClient

from chronikwerk.app.server import create_app
from tests.support.settings_factory import make_settings


def test_jobs_history_endpoint_returns_items(tmp_path, monkeypatch) -> None:
    app = create_app(
        make_settings(
            str(tmp_path),
            overrides={
                "observability": {
                    "history_enabled": True,
                    "history_bearer_token": "history-secret",
                }
            },
        )
    )
    import chronikwerk.app.routes.jobs as jobs_route

    def _stub_history(*, limit: int, ticket_id: int | None = None):
        assert limit == 50
        assert ticket_id == 123
        return [{"status": "processed", "ticket_id": 123}]

    monkeypatch.setattr(jobs_route, "read_history", _stub_history)

    client = TestClient(app)
    response = client.get(
        "/jobs/history?limit=50&ticket_id=123",
        headers={"Authorization": "Bearer history-secret"},
    )
    assert response.status_code == 200
    assert response.json() == {"entries": [{"status": "processed", "ticket_id": 123}]}


def test_jobs_history_default_is_disabled(tmp_path) -> None:
    response = TestClient(create_app(make_settings(str(tmp_path)))).get("/jobs/history")
    assert response.status_code == 404


def test_jobs_history_authentication_is_closed_by_default(tmp_path) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={
            "observability": {"history_enabled": True, "history_bearer_token": "history-secret"}
        },
    )
    client = TestClient(create_app(settings))
    assert client.get("/jobs/history").status_code == 401
    assert client.get("/jobs/history", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert (
        client.get("/jobs/history", headers={"Authorization": "Basic history-secret"}).status_code
        == 401
    )
