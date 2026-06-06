from __future__ import annotations

from fastapi.testclient import TestClient

from test.support.body_size_limit_helpers import check_request_too_large, post_oversized_json
from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.server import create_app
from zammad_pdf_archiver.config.settings import Settings


def _test_settings(storage_root: str) -> Settings:
    return make_settings(
        storage_root,
        overrides={
            "hardening": {
                "rate_limit": {"enabled": False, "rps": 1, "burst": 1},
                "body_size_limit": {"max_bytes": 10},
            }
        },
    )


def _body_limited_client(tmp_path) -> TestClient:  # noqa: ANN001
    return TestClient(create_app(_test_settings(str(tmp_path))))


def test_body_size_limit_triggers_on_ingest(tmp_path) -> None:
    client = _body_limited_client(tmp_path)

    resp = post_oversized_json(client, "/ingest")
    check_request_too_large(resp, request_id=True)


def test_body_size_limit_triggers_on_ingest_batch(tmp_path) -> None:
    client = _body_limited_client(tmp_path)

    resp = client.post(
        "/ingest/batch",
        content=b'[{"ticket":{"id":123}}]',
        headers={"Content-Type": "application/json"},
    )
    check_request_too_large(resp, request_id=True)


def test_body_size_limit_triggers_on_ingest_path_variants(tmp_path) -> None:
    client = _body_limited_client(tmp_path)

    for path in ("/ingest/", "/ingest%2F", "/ingest/batch/"):
        resp = post_oversized_json(client, path, follow_redirects=False)
        check_request_too_large(resp, request_id=True)
