from __future__ import annotations

from fastapi.testclient import TestClient

from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.server import create_app


def test_jobs_history_endpoint_returns_items(tmp_path, monkeypatch) -> None:
    app = create_app(make_settings(str(tmp_path)))
    import zammad_pdf_archiver.app.routes.jobs as jobs_route

    def _stub_history(*, limit: int, ticket_id: int | None = None):
        assert limit == 50
        assert ticket_id == 123
        return [{"status": "processed", "ticket_id": 123}]

    monkeypatch.setattr(jobs_route, "read_history", _stub_history)

    client = TestClient(app)
    response = client.get("/jobs/history?limit=50&ticket_id=123")
    assert response.status_code == 200
    assert response.json() == {"entries": [{"status": "processed", "ticket_id": 123}]}
