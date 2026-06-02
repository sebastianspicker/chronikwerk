from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from test.support.checks import check
from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.server import create_app, lifespan


@pytest.fixture(autouse=True)
def _reset_shutdown_flag() -> Generator[None, None, None]:
    """Ensure the global shutdown flag is reset after every test in this module."""
    from zammad_pdf_archiver.app.jobs.shutdown import clear_shutting_down

    yield
    clear_shutting_down()


def test_global_exception_handler_returns_consistent_api_error(tmp_path) -> None:
    app = create_app(make_settings(str(tmp_path)))

    @app.get("/boom")
    def _boom() -> dict[str, str]:
        raise RuntimeError("boom")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/boom", headers={"X-Request-Id": "req-boom-1"})

    check(not not response.status_code == 500, "assertion failed")
    check(
        not not response.json()
        == {
            "detail": "An internal server error occurred.",
            "code": "internal_error",
            "request_id": "req-boom-1",
        },
        "assertion failed",
    )
    check(not not response.headers.get("X-Request-Id") == "req-boom-1", "assertion failed")


def test_lifespan_with_settings_no_redis(tmp_path) -> None:
    """lifespan runs through startup/shutdown when settings has no redis_url."""
    settings = make_settings(str(tmp_path))

    from fastapi import FastAPI

    app = FastAPI(lifespan=lifespan)
    app.state.settings = settings

    client = TestClient(app)
    # TestClient context manager exercises the lifespan (startup + shutdown).
    with client:
        pass  # startup and shutdown execute without error


def test_lifespan_without_settings() -> None:
    """lifespan runs through startup/shutdown when app.state has no settings attribute."""
    from fastapi import FastAPI

    app = FastAPI(lifespan=lifespan)
    # Intentionally do NOT set app.state.settings

    client = TestClient(app)
    with client:
        pass  # should not raise
