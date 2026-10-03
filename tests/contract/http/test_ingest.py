"""Exercise the public HTTP ingestion contract and its scheduling handoff."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from chronikwerk.configuration.models import Settings
from chronikwerk.web.app import create_app
from tests.support.http_security_test_helpers import post_signed_json
from tests.support.scheduling import SchedulingSpy
from tests.support.settings_factory import make_settings

_WEBHOOK_SECRET = "test-webhook-secret"
_LEGACY_FORCE_REPROCESS_FIELD = "_force_reprocess"


def _settings(storage_root: str, *, overrides: dict[str, Any] | None = None) -> Settings:
    """Build validated HTTP-contract settings for one isolated archive root."""
    return make_settings(storage_root, secret=_WEBHOOK_SECRET, overrides=overrides)


def _client(tmp_path, *, accept: bool = True, overrides: dict[str, Any] | None = None):
    """Create a test client with an observable scheduler boundary."""
    scheduler = SchedulingSpy(accept=accept)
    app = create_app(_settings(str(tmp_path), overrides=overrides), scheduler=scheduler)
    return TestClient(app), scheduler


def _post(client: TestClient, path: str, payload: Any, **headers: str):
    """Post one correctly signed JSON request to the test application."""
    return post_signed_json(
        client,
        path,
        payload,
        secret=_WEBHOOK_SECRET,
        extra_headers=headers or None,
    )


def test_ingest_validates_payload_and_hands_off_sanitized_job(tmp_path) -> None:
    client, scheduler = _client(tmp_path)

    response = _post(
        client,
        "/ingest",
        {"ticket": {"id": 123}, _LEGACY_FORCE_REPROCESS_FIELD: True},
        **{"X-Zammad-Delivery": "delivery-xyz"},
    )

    assert response.status_code == 202
    assert response.json() == {"status": "accepted", "ticket_id": 123}
    assert response.headers["X-Request-Id"]
    assert len(scheduler.scheduled) == 1
    job = scheduler.scheduled[0]
    delivery_id, payload = job.delivery_id, job.payload
    assert delivery_id == "delivery-xyz"
    assert payload["ticket"]["id"] == 123
    assert job.request_id
    assert _LEGACY_FORCE_REPROCESS_FIELD not in payload
    assert job.force_reprocess is False


def test_ingest_rejects_invalid_payload_without_scheduling(tmp_path) -> None:
    client, scheduler = _client(tmp_path)

    response = _post(client, "/ingest", {"ticket_id": True})

    assert response.status_code == 422
    assert scheduler.scheduled == []


def test_batch_assigns_delivery_suffixes_and_keeps_per_item_payloads(tmp_path) -> None:
    client, scheduler = _client(tmp_path)

    response = _post(
        client,
        "/ingest/batch",
        [{"ticket": {"id": 111}}, {"ticket_id": 222}],
        **{"X-Zammad-Delivery": "delivery-batch"},
    )

    assert response.status_code == 202
    assert response.json() == {"status": "accepted", "count": 2}
    assert [(job.delivery_id, job.payload.get("ticket_id")) for job in scheduler.scheduled] == [
        ("delivery-batch:0", None),
        ("delivery-batch:1", 222),
    ]
    assert scheduler.scheduled[0].payload["ticket"]["id"] == 111


def test_ingest_dry_runs_and_capacity_failures_do_not_schedule(tmp_path) -> None:
    client, scheduler = _client(tmp_path, accept=False)

    dry_run = _post(client, "/ingest?dry_run=true", {"ticket_id": 123})
    overloaded = _post(client, "/ingest", {"ticket_id": 123})

    assert dry_run.json() == {"status": "dry_run_accepted", "ticket_id": 123}
    assert overloaded.status_code == 503
    assert overloaded.json()["code"] == "job_capacity_exhausted"
    assert len(scheduler.scheduled) == 1
    assert scheduler.scheduled[0].delivery_id is None
    assert scheduler.scheduled[0].payload["ticket_id"] == 123
    assert scheduler.scheduled[0].request_id


def test_retry_requires_token_and_hands_off_authorized_request(tmp_path) -> None:
    client, scheduler = _client(tmp_path, overrides={"retry_bearer_token": "retry-token"})

    unauthorized = client.post("/retry/987")
    authorized = client.post("/retry/987", headers={"Authorization": "Bearer retry-token"})

    assert unauthorized.status_code == 401
    assert authorized.status_code == 202
    assert authorized.json() == {"status": "accepted", "ticket_id": 987}
    assert [ticket_id for ticket_id, _request_id in scheduler.retries] == [987]
    assert scheduler.retries[0][1]


def test_request_identifier_is_preserved_only_when_valid(tmp_path) -> None:
    client, _scheduler = _client(tmp_path)

    accepted = _post(client, "/ingest", {"ticket_id": 1}, **{"X-Request-Id": "request-1"})
    replaced = _post(client, "/ingest", {"ticket_id": 2}, **{"X-Request-Id": "not valid"})

    assert accepted.headers["X-Request-Id"] == "request-1"
    assert replaced.headers["X-Request-Id"] != "not valid"
