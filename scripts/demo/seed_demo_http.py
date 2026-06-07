from __future__ import annotations

import time
from collections import Counter
from typing import Any

import httpx


def wait_for_ready(
    client: httpx.Client,
    label: str,
    url: str,
    *,
    timeout_s: float = 60.0,
) -> None:
    deadline = time.monotonic() + timeout_s
    last_error = ""
    while time.monotonic() < deadline:
        try:
            response = client.get(url)
            if response.status_code == 200:
                return
            last_error = f"HTTP {response.status_code}"
        except Exception as exc:  # pragma: no cover - defensive
            last_error = str(exc)
        time.sleep(1.0)
    raise RuntimeError(f"{label} not ready at {url}: {last_error}")


def request_json(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: Any | None = None,
) -> tuple[int, Any]:
    response = client.request(method, url, headers=headers, json=json_body)
    text = response.text
    try:
        parsed: Any = response.json()
    except Exception:
        parsed = {"raw": text}
    return response.status_code, parsed


def auth_header(admin_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_token}"}


def submit_seed_ingests(
    client: httpx.Client,
    *,
    archiver_url: str,
    seed_plan: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    ingests: list[dict[str, Any]] = []
    for item in seed_plan:
        ticket_id = int(item["ticket_id"])
        delivery_id = str(item.get("delivery_id") or f"demo-delivery-{ticket_id}")
        user_login = str(item.get("user_login") or "demo.agent")
        expected_status = str(item.get("expected_status") or "unknown")

        status_code, payload = request_json(
            client,
            "POST",
            f"{archiver_url}/ingest",
            headers={"X-Zammad-Delivery": delivery_id},
            json_body={"ticket": {"id": ticket_id}, "user": {"login": user_login}},
        )
        ingests.append(
            {
                "ticket_id": ticket_id,
                "delivery_id": delivery_id,
                "expected_status": expected_status,
                "http_status": status_code,
                "response": payload,
            }
        )
    return ingests


def poll_history(
    client: httpx.Client,
    *,
    archiver_url: str,
    admin_token: str,
    target_count: int,
) -> dict[str, Any]:
    history_payload: dict[str, Any] = {}
    for _ in range(30):
        history_code, data = request_json(
            client,
            "GET",
            f"{archiver_url}/admin/api/history?limit=200",
            headers=auth_header(admin_token),
        )
        if history_code == 200 and isinstance(data, dict):
            history_payload = data
            if int(data.get("count", 0)) >= target_count:
                break
        time.sleep(1.0)
    return history_payload


def history_status_counts(history_payload: dict[str, Any]) -> dict[str, int]:
    raw_items = history_payload.get("items") if isinstance(history_payload, dict) else []
    items = raw_items if isinstance(raw_items, list) else []
    return dict(
        Counter(str(item.get("status", "unknown")) for item in items if isinstance(item, dict))
    )


def reset_mock_zammad(client: httpx.Client, mock_url: str) -> tuple[int, Any]:
    reset_code, reset_payload = request_json(
        client,
        "POST",
        f"{mock_url}/__demo/reset",
    )
    if reset_code != 200:
        raise RuntimeError(f"mock reset failed ({reset_code}): {reset_payload}")
    return reset_code, reset_payload


def fetch_queue_and_mock_state(
    client: httpx.Client,
    *,
    archiver_url: str,
    mock_url: str,
    admin_token: str,
) -> tuple[int, Any, int, Any]:
    queue_code, queue_payload = request_json(
        client,
        "GET",
        f"{archiver_url}/admin/api/queue/stats",
        headers=auth_header(admin_token),
    )
    mock_state_code, mock_state_payload = request_json(
        client,
        "GET",
        f"{mock_url}/__demo/state",
    )
    return queue_code, queue_payload, mock_state_code, mock_state_payload
