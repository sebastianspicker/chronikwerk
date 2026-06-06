from __future__ import annotations

import pytest

from test.support.credentials import fake_credential
from zammad_pdf_archiver.app.jobs import ticket_stores
from zammad_pdf_archiver.app.jobs.shutdown import clear_shutting_down
from zammad_pdf_archiver.config.settings import Settings


def make_ticket_store_settings(
    *,
    ttl: int = 3600,
    backend: str = "memory",
    redis_url: str | None = None,
) -> Settings:
    overrides: dict = {
        "workflow": {
            "delivery_id_ttl_seconds": ttl,
            "idempotency_backend": backend,
        },
    }
    if redis_url is not None:
        overrides["workflow"]["redis_url"] = redis_url
    return Settings.from_mapping(
        {
            "zammad": {
                "base_url": "https://zammad.example.local",
                "api_token": fake_credential("test-token"),
            },
            "storage": {"root": "/var/lib/test-ticket-stores"},
            "hardening": {
                "webhook": {
                    "allow_unsigned": True,
                    "allow_unsigned_when_no_secret": bool(1),
                }
            },
            **overrides,
        }
    )


@pytest.fixture(autouse=True)
def reset_stores():
    """Ensure clean module-level state before and after every test."""
    ticket_stores._reset_for_tests()
    clear_shutting_down()
    yield
    ticket_stores._reset_for_tests()
    clear_shutting_down()


class FakeCounter:
    def __init__(self) -> None:
        self.count = 0

    def inc(self) -> None:
        self.count += 1
