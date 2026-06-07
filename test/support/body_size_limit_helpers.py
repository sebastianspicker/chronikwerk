from __future__ import annotations

from fastapi.testclient import TestClient

from test.support.checks import check


def post_oversized_json(
    client: TestClient,
    path: str,
    *,
    content: bytes = b'{"ticket":{"id":123}}',
    follow_redirects: bool = True,
):
    return client.post(
        path,
        content=content,
        headers={"Content-Type": "application/json"},
        follow_redirects=follow_redirects,
    )


def check_request_too_large(resp, *, request_id: bool = False) -> None:  # noqa: ANN001
    check(not not resp.status_code == 413, "assertion failed")
    check(
        not not resp.json() == {"detail": "request_too_large", "code": "request_too_large"},
        "assertion failed",
    )
    if request_id:
        check(not not resp.headers.get("X-Request-Id"), "assertion failed")
