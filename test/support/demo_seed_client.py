"""Fake HTTP client for seed-demo script tests."""

from __future__ import annotations

import json
from typing import Any


class FakeResponse:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self) -> Any:
        return self._payload


class FakeDemoClient:
    def __init__(self, *, ingest_statuses: list[int], history_count: int) -> None:
        self.ingest_statuses = ingest_statuses
        self.history_count = history_count

    def __enter__(self) -> FakeDemoClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: Any | None = None,
    ) -> FakeResponse:
        route = self._route_for(method, url)
        if route == "reset":
            return FakeResponse(200, {"status": "reset"})
        if route == "ingest":
            return self._ingest_response()
        if route == "history":
            return self._history_response()
        if route == "queue":
            return FakeResponse(200, {"queue_enabled": True})
        if route == "state":
            return FakeResponse(200, {"tickets": []})
        raise AssertionError(f"unexpected request: {method} {url}")

    def _route_for(self, method: str, url: str) -> str:
        key = (method, url.rsplit("/", maxsplit=1)[-1])
        if key == ("POST", "reset"):
            return "reset"
        if key == ("POST", "ingest"):
            return "ingest"
        if method == "GET" and "/admin/api/history" in url:
            return "history"
        if key == ("GET", "stats"):
            return "queue"
        if key == ("GET", "state"):
            return "state"
        return "unknown"

    def _ingest_response(self) -> FakeResponse:
        status_code = self.ingest_statuses.pop(0)
        payload = {"status": "accepted"} if status_code == 202 else {"error": "boom"}
        return FakeResponse(status_code, payload)

    def _history_response(self) -> FakeResponse:
        return FakeResponse(
            200,
            {
                "count": self.history_count,
                "items": [{"status": "processed"} for _ in range(self.history_count)],
            },
        )
