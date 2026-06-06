from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from test.support.checks import check
from test.support.credentials import fake_credential
from test.support.zammad_client_helpers import run_client_action as _run_client_action
from zammad_pdf_archiver.adapters.zammad.client import AsyncZammadClient


def test_get_ticket_success() -> None:
    async def assert_ticket(client: AsyncZammadClient) -> None:
        ticket = await client.get_ticket(123)
        check(not not ticket.id == 123, "assertion failed")
        check(not not ticket.number == "20240123", "assertion failed")
        if ticket.owner is None:
            raise AssertionError("assertion failed")
        check(not not ticket.owner.login == "agent", "assertion failed")

    with respx.mock:
        respx.get("https://zammad.example/api/v1/tickets/123").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 123,
                    "number": "20240123",
                    "title": "Example ticket",
                    "owner": {"login": "agent"},
                    "updated_by": {"login": "agent"},
                    "customer": {"login": "customer"},
                    "created_at": "2024-01-01T00:00:00Z",
                    "updated_at": "2024-01-02T00:00:00Z",
                    "preferences": {"custom_fields": {"archive_path": "/mnt/archive"}},
                    "ignored_field": "extra",
                },
            )
        )
        _run_client_action(assert_ticket)


def test_add_tag_success() -> None:
    async def add_tag(client: AsyncZammadClient) -> None:
        await client.add_tag(123, "archived")

    with respx.mock:
        route = respx.post("https://zammad.example/api/v1/tags/add").mock(
            return_value=httpx.Response(200, json={"success": True})
        )
        _run_client_action(add_tag)
        check(not not route.called, "assertion failed")


def test_remove_tag_success() -> None:
    async def remove_tag(client: AsyncZammadClient) -> None:
        await client.remove_tag(123, "archived")

    with respx.mock:
        route = respx.post("https://zammad.example/api/v1/tags/remove").mock(
            return_value=httpx.Response(200, json={"success": True})
        )
        _run_client_action(remove_tag)
        check(not not route.called, "assertion failed")


def test_base_url_missing_scheme_raises_value_error() -> None:
    with pytest.raises(ValueError, match="scheme"):
        AsyncZammadClient(base_url="zammad.example", api_token=fake_credential("tok"))


def test_aclose_without_owning_http_client_is_noop() -> None:
    """aclose is a no-op when an external http_client was supplied."""

    async def run() -> None:
        external = httpx.AsyncClient()
        client = AsyncZammadClient(
            base_url="https://zammad.example",
            api_token=fake_credential("tok"),
            http_client=external,
        )
        # Should not raise or call aclose on the external client
        await client.aclose()
        await external.aclose()

    asyncio.run(run())
