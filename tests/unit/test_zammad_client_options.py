"""Keep grouped Zammad transport options compatible with existing keyword callers."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import SecretStr

from chronikwerk.adapters.zammad.client import AsyncZammadClient, ZammadClientOptions
from chronikwerk.config.settings import ZammadConnection
from tests.support.zammad_client_helpers import _test_runtime


def _connection() -> ZammadConnection:
    """Return a safe configured connection for constructor-conflict tests."""
    return ZammadConnection(
        origin="https://zammad.example",
        api_token=SecretStr("test-token"),
    )


def test_transport_keyword_contract_is_preserved() -> None:
    client = AsyncZammadClient(
        base_url="https://zammad.example",
        api_token="test-token",
        timeout_seconds=3.0,
        verify_tls=False,
        trust_env=True,
        allow_private_networks=True,
        _runtime=_test_runtime(allow_private_networks=False),
    )

    assert client._dns_timeout_seconds == 3.0
    assert client._allow_private_networks is True
    asyncio.run(client.aclose())


def test_safe_direct_transport_does_not_require_test_runtime() -> None:
    client = AsyncZammadClient(
        base_url="https://zammad.example",
        api_token="test-token",
    )

    asyncio.run(client.aclose())


def test_grouped_and_keyword_transport_options_are_mutually_exclusive() -> None:
    with pytest.raises(TypeError, match="options cannot be combined"):
        AsyncZammadClient(
            base_url="https://zammad.example",
            api_token="test-token",
            options=ZammadClientOptions(),
            trust_env=True,
            _runtime=_test_runtime(),
        )


def test_connection_rejects_direct_credentials() -> None:
    with pytest.raises(TypeError, match="connection cannot be combined with base_url or api_token"):
        AsyncZammadClient(
            connection=_connection(),
            base_url="https://another-zammad.example",
            api_token="another-token",
        )


@pytest.mark.parametrize("transport_option", ("options", "trust_env"))
def test_connection_rejects_transport_options(transport_option: str) -> None:
    with pytest.raises(TypeError, match="connection cannot be combined with transport options"):
        if transport_option == "options":
            AsyncZammadClient(connection=_connection(), options=ZammadClientOptions())
        else:
            AsyncZammadClient(connection=_connection(), trust_env=True)


@pytest.mark.parametrize(
    ("base_url", "api_token"),
    ((None, None), ("https://zammad.example", None), (None, "test-token")),
)
def test_direct_construction_requires_both_credentials(
    base_url: str | None, api_token: str | None
) -> None:
    with pytest.raises(
        TypeError, match="base_url and api_token are required when connection is not provided"
    ):
        AsyncZammadClient(base_url=base_url, api_token=api_token)
