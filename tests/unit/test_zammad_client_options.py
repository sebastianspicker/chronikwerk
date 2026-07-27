"""Keep grouped Zammad transport options compatible with existing keyword callers."""

from __future__ import annotations

import asyncio

import pytest

from chronikwerk.adapters.zammad.client import AsyncZammadClient, ZammadClientOptions
from tests.support.zammad_client_helpers import _test_runtime


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


def test_grouped_and_keyword_transport_options_are_mutually_exclusive() -> None:
    with pytest.raises(TypeError, match="options cannot be combined"):
        AsyncZammadClient(
            base_url="https://zammad.example",
            api_token="test-token",
            options=ZammadClientOptions(),
            trust_env=True,
            _runtime=_test_runtime(),
        )
