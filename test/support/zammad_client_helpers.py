from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import respx

from test.support.credentials import fake_credential
from zammad_pdf_archiver.adapters.zammad.client import AsyncZammadClient


async def no_sleep(_: float) -> None:
    return None


def run_client_action(
    action: Callable[[AsyncZammadClient], Awaitable[None]],
    *,
    api_token: str = "test-token",
) -> None:
    async def run() -> None:
        async with AsyncZammadClient(
            base_url="https://zammad.example",
            api_token=fake_credential(api_token),
            sleep=no_sleep,
        ) as client:
            await action(client)

    asyncio.run(run())


def mock_tags_response(payload: Any) -> None:
    respx.get(
        "https://zammad.example/api/v1/tags",
        params={"object": "Ticket", "o_id": "123"},
    ).mock(return_value=httpx.Response(200, json=payload))
