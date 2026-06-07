from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from test.support.checks import check
from test.support.zammad_client_helpers import mock_tags_response as _mock_tags_response
from test.support.zammad_client_helpers import run_client_action as _run_client_action
from zammad_pdf_archiver.adapters.zammad.client import AsyncZammadClient
from zammad_pdf_archiver.adapters.zammad.errors import ClientError


def test_list_tags_success() -> None:
    async def assert_tags(client: AsyncZammadClient) -> None:
        tags = await client.list_tags(123)
        check(not not tags.root == ["pdf:sign", "archived"], "assertion failed")

    with respx.mock:
        _mock_tags_response(["pdf:sign", "archived"])
        _run_client_action(assert_tags)


@pytest.mark.parametrize(
    ("method_name", "endpoint"),
    [
        ("add_tag", "api/v1/tags/add"),
        ("remove_tag", "api/v1/tags/remove"),
    ],
)
@pytest.mark.parametrize(
    "body",
    [
        {"success": False},
        {"success": "false"},
        {},
        ["success"],
    ],
)
def test_tag_mutation_requires_confirmed_success(
    method_name: str, endpoint: str, body: Any
) -> None:
    async def assert_mutation_rejected(client: AsyncZammadClient) -> None:
        method = getattr(client, method_name)
        with pytest.raises(ClientError, match="did not confirm success"):
            await method(123, "archived")

    with respx.mock:
        route = respx.post(f"https://zammad.example/{endpoint}").mock(
            return_value=httpx.Response(200, json=body)
        )
        _run_client_action(assert_mutation_rejected)
        check(not not route.called, "assertion failed")


def test_list_tags_dict_response_with_tags_key() -> None:
    """list_tags handles Zammad versions that return {"tags": [...]} wrapper."""

    async def assert_tags(client: AsyncZammadClient) -> None:
        tags = await client.list_tags(123)
        check(not not tags.root == ["foo", "bar"], "assertion failed")

    with respx.mock:
        _mock_tags_response({"tags": ["foo", "bar"]})
        _run_client_action(assert_tags)


def test_list_tags_invalid_format_raises_client_error() -> None:
    """list_tags raises ClientError when tags value cannot be parsed as list[str]."""

    async def assert_invalid_tags(client: AsyncZammadClient) -> None:
        with pytest.raises(ClientError, match="unexpected"):
            await client.list_tags(123)

    with respx.mock:
        _mock_tags_response({"tags": {"not": "a-list"}})
        _run_client_action(assert_invalid_tags)
