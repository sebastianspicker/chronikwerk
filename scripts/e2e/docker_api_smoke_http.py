from __future__ import annotations

import time
from typing import Any

import httpx

from scripts.e2e.docker_api_smoke_contracts import E2EFailure, assert_expected_statuses


def wait_http_ok(client: httpx.Client, label: str, url: str, *, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    last_error = ""
    while time.monotonic() < deadline:
        try:
            response = client.get(url)
            if response.status_code == 200:
                return
            last_error = f"HTTP {response.status_code}"
        except Exception as exc:
            last_error = str(exc)
        time.sleep(1.0)
    raise E2EFailure(f"readiness phase: {label} not ready at {url}: {last_error}")


def request_json(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: Any | None = None,
) -> tuple[int, Any]:
    response = client.request(method, url, headers=headers, json=json_body)
    try:
        return response.status_code, response.json()
    except Exception:
        return response.status_code, {"raw": response.text}


def wait_for_statuses(
    client: httpx.Client,
    *,
    archiver_url: str,
    admin_token: str,
    expected: dict[int, str],
    timeout_s: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    last_error = ""
    while time.monotonic() < deadline:
        status_code, payload = request_json(
            client,
            "GET",
            f"{archiver_url}/admin/api/history?limit=200",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        if status_code == 200 and isinstance(payload, dict):
            try:
                assert_expected_statuses(payload, expected)
                return payload
            except E2EFailure as exc:
                last_error = str(exc)
        else:
            last_error = f"HTTP {status_code}: {payload}"
        time.sleep(1.0)
    raise E2EFailure(f"history phase: timed out waiting for expected statuses: {last_error}")


def seed_dataset(
    client: httpx.Client,
    *,
    archiver_url: str,
    mock_url: str,
    dataset: dict[str, Any],
) -> None:
    reset_code, reset_payload = request_json(client, "POST", f"{mock_url}/__demo/reset")
    if reset_code != 200:
        raise E2EFailure(f"ingest phase: mock reset failed ({reset_code}): {reset_payload}")

    seed_plan = dataset["seed_plan"]
    for item in seed_plan:
        ticket_id = int(item["ticket_id"])
        delivery_id = str(item.get("delivery_id") or f"e2e-delivery-{ticket_id}")
        user_login = str(item.get("user_login") or "e2e.agent")
        status_code, payload = request_json(
            client,
            "POST",
            f"{archiver_url}/ingest",
            headers={"X-Zammad-Delivery": delivery_id},
            json_body={"ticket": {"id": ticket_id}, "user": {"login": user_login}},
        )
        if status_code != 202:
            raise E2EFailure(
                f"ingest phase: ticket {ticket_id} returned HTTP {status_code}: {payload}"
            )


def retry_ticket(
    client: httpx.Client,
    *,
    archiver_url: str,
    admin_token: str,
    ticket_id: int,
) -> None:
    status_code, payload = request_json(
        client,
        "POST",
        f"{archiver_url}/admin/api/retry/{ticket_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    if status_code != 200 or not isinstance(payload, dict) or payload.get("status") != "accepted":
        raise E2EFailure(f"admin retry phase: HTTP {status_code}: {payload}")
